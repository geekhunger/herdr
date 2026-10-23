import concurrent.futures
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Store, next_time, preview, schedule_from_text
from mcp_server import call
from runner import codex_command, tick
from sidebar import publish

APP = Path(__file__).resolve().parents[1]


class SchedulesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.values = {"name": "Testjob", "prompt": "Prüfe nur diesen Auftrag.", "cwd": str(self.root),
                       "schedule": {"type": "daily", "time": "09:15"}, "enabled": False}

    def tearDown(self):
        self.temp.cleanup()

    def stamp(self, value):
        return dt.datetime.fromisoformat(value).timestamp()

    def test_times_and_dst(self):
        spec = {"type": "daily", "time": "09:15"}
        self.assertEqual(preview(spec, after=self.stamp("2026-03-28T10:00:00+01:00"), count=1), ["2026-03-29T09:15:00+02:00"])
        self.assertEqual(preview(spec, after=self.stamp("2026-10-24T10:00:00+02:00"), count=1), ["2026-10-25T09:15:00+01:00"])
        missing = {"type": "daily", "time": "02:30"}
        self.assertEqual(preview(missing, after=self.stamp("2026-03-28T03:00:00+01:00"), count=1), ["2026-03-30T02:30:00+02:00"])
        self.assertEqual(preview(missing, after=self.stamp("2026-10-25T02:31:00+02:00"), count=1), ["2026-10-26T02:30:00+01:00"])
        weekly = schedule_from_text("weekly mo,fr 09:00")
        self.assertEqual(preview(weekly, after=self.stamp("2026-10-23T10:00:00+02:00"), count=1), ["2026-10-26T09:00:00+01:00"])
        with self.assertRaises(ValueError):
            next_time({"type": "once", "at": "2026-03-29T02:30:00"}, "Europe/Berlin", self.stamp("2026-03-28T00:00:00+01:00"))

    def test_validation(self):
        for schedule in ({"type": "interval", "minutes": -2}, {"type": "daily", "time": "25:00"},
                         {"type": "cron", "expression": "invalid"}, {"type": "weekly", "time": "09:00", "days": [7]}):
            with self.assertRaises(ValueError):
                self.store.create({**self.values, "schedule": schedule})
        with self.assertRaises(ValueError):
            self.store.create({**self.values, "cwd": str(self.root / "missing")})
        with self.assertRaises(ValueError):
            self.store.create({**self.values, "enabled": True, "schedule": {"type": "once", "at": "2020-01-01T09:00:00+01:00"}})

    def test_read_connections_close_without_waiting_for_gc(self):
        with self.store.connect() as connection:
            connection.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_sidebar_snapshot(self):
        paused = self.store.create({**self.values, "name": "Pausiert\nmit Zeile"})
        active = self.store.create({**self.values, "name": "Aktiv", "enabled": True})
        run = self.store.enqueue(paused["id"])
        publish(self.store)
        data = json.loads((self.store.root / "sidebar.json").read_text())
        self.assertEqual((data["total"], data["active"]), (2, 1))
        self.assertEqual(data["jobs"][0]["status"], "queued")
        self.assertEqual(data["jobs"][0]["name"], "Pausiert mit Zeile")
        self.assertEqual(data["jobs"][1]["next"], dt.datetime.fromisoformat(active["next_run_at"]).strftime("%d.%m. %H:%M"))
        self.store.archive(paused["id"])
        publish(self.store)
        self.assertEqual(json.loads((self.store.root / "sidebar.json").read_text())["total"], 1)

    def test_shared_crud_revision_archive(self):
        job = call(self.store, "schedules_create", {**self.values, "request_key": "same"})
        self.assertEqual(call(self.store, "schedules_create", {**self.values, "request_key": "same"})["id"], job["id"])
        changed = call(self.store, "schedules_update", {"id": job["id"], "changes": {"prompt": "Neuer Prompt\nMit Umlauten äöü"}, "expected_revision": job["revision"]})
        self.assertEqual(self.store.get(job["id"])["prompt"], changed["prompt"])
        with self.assertRaises(ValueError):
            self.store.update(job["id"], {"name": "Stale"}, expected_revision=job["revision"])
        call(self.store, "schedules_resume", {"id": job["id"]})
        self.assertTrue(self.store.get(job["id"])["enabled"])
        call(self.store, "schedules_pause", {"id": job["id"]})
        self.assertIsNone(self.store.get(job["id"])["next_run_at"])
        call(self.store, "schedules_delete", {"id": job["id"]})
        self.assertEqual(self.store.list(), [])
        restored = call(self.store, "schedules_restore", {"id": job["id"]})
        self.assertFalse(restored["enabled"])
        self.assertIn("Umlauten", restored["prompt"])

    def test_concurrent_enqueue_and_missed_runs(self):
        job = self.store.create({**self.values, "enabled": True, "schedule": {"type": "interval", "minutes": 1}})
        edited = self.store.update(job["id"], {"prompt": "Changed prompt", "schedule": job["schedule"], "enabled": True})
        self.assertEqual(edited["next_run_ts"], job["next_run_ts"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            queued = list(executor.map(lambda _: self.store.enqueue(job["id"]), range(12)))
        self.assertEqual(len({run["run_id"] for run in queued}), 1)
        self.assertEqual(len(self.store.runs(job["id"])), 1)
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET next_run=? WHERE id=?", (time.time() - 36000, job["id"]))
        claimed = tick(self.store)
        self.assertEqual(claimed, queued[0]["run_id"])
        self.assertIsNone(tick(self.store))
        self.assertEqual(len(self.store.runs(job["id"])), 1)
        self.assertGreater(self.store.get(job["id"])["next_run_ts"], time.time())

    def test_stdio_mcp(self):
        env = {**os.environ, "HERDR_SCHEDULES_HOME": str(self.store.root)}
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "schedules_create", "arguments": self.values}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "schedules_list", "arguments": {}}},
        ]
        process = subprocess.run([sys.executable, str(APP / "main.py"), "mcp"], env=env, input="\n".join(map(json.dumps, messages))+"\n", text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2, 3, 4])
        self.assertGreaterEqual(len(replies[1]["result"]["tools"]), 12)
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertEqual(len(json.loads(replies[3]["result"]["content"][0]["text"])["jobs"]), 1)

    def fake_codex(self):
        path = self.root / "fake-codex"
        path.write_text('''#!/usr/bin/python3
import json, pathlib, sys, time
args=sys.argv[1:]
prompt=sys.stdin.read()
pathlib.Path("codex-args.json").write_text(json.dumps(args))
if "SLOW" in prompt:
    time.sleep(120)
if "FORCE_FAIL" in prompt:
    print("forced failure",file=sys.stderr)
    sys.exit(3)
answer=pathlib.Path(args[args.index("-o")+1])
answer.write_text("CONTINUED" if "resume" in args else "FRESH")
print(json.dumps({"type":"thread.started","thread_id":"00000000-0000-0000-0000-000000000001"}))
print(json.dumps({"type":"turn.completed"}))
''')
        path.chmod(0o700)
        return path

    def launch_worker(self, job):
        queued = self.store.enqueue(job["id"])
        run_id = tick(self.store)
        self.assertEqual(run_id, queued["run_id"])
        env = {**os.environ, "HERDR_SCHEDULES_HOME": str(self.store.root), "HERDR_SCHEDULES_CODEX": str(self.fake_codex()), "HERDR_SCHEDULES_TEST": "1"}
        process = subprocess.Popen([sys.executable, str(APP / "main.py"), "worker", run_id], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return run_id, process

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; live Windows smoke test required")
    def test_worker_fresh_continue_and_failure(self):
        job = self.store.create({**self.values, "context_mode": "continue"})
        run_id, process = self.launch_worker(job)
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, err)
        result = self.store.result(run_id)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["answer"], "FRESH")
        self.assertTrue(self.store.get(job["id"])["session_id"])
        run_id, process = self.launch_worker(self.store.get(job["id"]))
        process.communicate(timeout=15)
        self.assertEqual(self.store.result(run_id)["answer"], "CONTINUED")
        args = json.loads((self.root / "codex-args.json").read_text())
        self.assertIn("--no-daemon", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        failed = self.store.create({**self.values, "prompt": "FORCE_FAIL"})
        run_id, process = self.launch_worker(failed)
        process.communicate(timeout=15)
        self.assertEqual(self.store.run(run_id)["status"], "failed")
        self.assertIn("forced failure", self.store.result(run_id)["stderr"])

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; live Windows smoke test required")
    def test_cancellation(self):
        job = self.store.create({**self.values, "prompt": "SLOW"})
        run_id, process = self.launch_worker(job)
        try:
            deadline=time.time()+10
            while not self.store.run(run_id)["pid"] and time.time()<deadline:
                time.sleep(.05)
            self.store.cancel(run_id)
            process.communicate(timeout=12)
            self.assertEqual(self.store.run(run_id)["status"], "cancelled")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; live Windows smoke test required")
    def test_daemon_once(self):
        at = dt.datetime.fromtimestamp(time.time()+2, dt.timezone.utc).isoformat()
        job = self.store.create({**self.values, "enabled": True, "schedule": {"type": "once", "at": at}})
        env = {**os.environ, "HERDR_SCHEDULES_HOME": str(self.store.root), "HERDR_SCHEDULES_CODEX": str(self.fake_codex()), "HERDR_SCHEDULES_TEST": "1"}
        process = subprocess.Popen([sys.executable, str(APP / "main.py"), "daemon"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline=time.time()+20
            while time.time()<deadline:
                runs=self.store.runs(job["id"])
                if runs and runs[0]["status"] in ("succeeded","failed"):
                    break
                time.sleep(.2)
            self.assertTrue(self.store.health()["healthy"])
            self.assertEqual(self.store.runs(job["id"])[0]["status"], "succeeded")
            self.assertFalse(self.store.get(job["id"])["enabled"])
            self.assertEqual(len(self.store.runs(job["id"])), 1)
        finally:
            process.terminate()
            process.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main()
