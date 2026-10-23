"""Independent launchd worker and scheduler; executes the Codex CLI directly."""
import locking as fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from core import Store, next_time
from sidebar import publish


def clean_environment():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("HERDR_") and key != "HERDR_SCHEDULES_HOME" and key != "HERDR_SCHEDULES_CODEX":
            env.pop(key, None)
        if key in ("CODEX_THREAD_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_CI"):
            env.pop(key, None)
    if os.name != "nt":
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
    env["NO_COLOR"] = "1"
    return env


def codex_command(job, directory, binary=None):
    binary = binary or os.environ.get("HERDR_SCHEDULES_CODEX") or shutil.which("codex", path=clean_environment()["PATH"])
    if not binary:
        raise ValueError("Codex CLI fehlt im PATH des Schedule-Dienstes.")
    command = [binary, "--no-daemon", "-a", "never", "-s", job["sandbox"]]
    if job["model"]:
        command += ["-m", job["model"]]
    if job["reasoning_effort"]:
        command += ["-c", "model_reasoning_effort=" + json.dumps(job["reasoning_effort"])]
    command += ["exec", "--json", "--skip-git-repo-check", "-C", job["cwd"],
                "-o", str(directory / "answer.txt")]
    if job["context_mode"] == "continue" and job["session_id"]:
        command += ["resume", job["session_id"], "-"]
    else:
        command += ["-"]
    return command


def terminate(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        process.wait(timeout=5)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass


def notify(job, status):
    if os.environ.get("HERDR_SCHEDULES_TEST") == "1":
        return
    try:
        subprocess.run([shutil.which("herdr") or "herdr", "notification", "show",
                        f"Schedule: {job['name']}", "--body",
                        f"{status} · Ergebnis: Ctrl+F → C → Läufe", "--sound",
                        "done" if status == "succeeded" else "request"],
                       env=clean_environment(), timeout=3, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass  # An unattached/stopped Herdr never prevents logging a result.


def worker(store, run_id):
    run = store.run(run_id)
    directory = store.root / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (directory / "worker.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    stopped = [False]
    def stop(_signum, _frame):
        stopped[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    job = run["snapshot"]
    process = None
    status, error, return_code, session_id = "failed", None, None, None
    try:
        if run["status"] != "running":
            return
        with store.transaction() as db:
            db.execute("UPDATE runs SET worker_pid=?,outdir=? WHERE id=?", (os.getpid(), str(directory), run_id))
        prompt = ("This is a scheduled local Codex task. Follow the saved user request below. "
                  "Report the actual outcome and any blockers.\n\n" + job["prompt"])
        # Use a file for stdin: long prompts never block a pipe, and no shell
        # interpretation can occur. Logs retain the exact approved job snapshot.
        input_file = directory / "prompt.txt"
        input_file.write_text(prompt, encoding="utf-8")
        with input_file.open("rb") as incoming, (directory / "events.jsonl").open("wb") as output, (directory / "stderr.log").open("wb") as errors:
            process = subprocess.Popen(codex_command(job, directory), stdin=incoming,
                                       stdout=output, stderr=errors, cwd=job["cwd"],
                                       env=clean_environment(), start_new_session=True)
            with store.transaction() as db:
                db.execute("UPDATE runs SET pid=? WHERE id=?", (process.pid, run_id))
            deadline = time.time() + job["timeout_minutes"] * 60
            while process.poll() is None:
                if stopped[0] or store.run(run_id)["cancel_requested"]:
                    status, error = "cancelled", "Lauf abgebrochen."
                    terminate(process)
                    break
                if time.time() > deadline:
                    status, error = "timed_out", "Laufzeitlimit erreicht."
                    terminate(process)
                    break
                time.sleep(0.3)
            return_code = process.wait()
        event_error = None
        with (directory / "events.jsonl").open(encoding="utf-8", errors="replace") as events:
            for line in events:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    session_id = event.get("thread_id")
                if event.get("type") == "turn.failed":
                    event_error = str(event.get("error", "Codex turn failed"))
        if error is None:
            if return_code != 0 or event_error:
                status, error = "failed", event_error or f"Codex endete mit Exit-Code {return_code}."
            elif not (directory / "answer.txt").exists() or not (directory / "answer.txt").read_text(encoding="utf-8", errors="replace").strip():
                status, error = "failed", "Codex lieferte keine abschließende Antwort; siehe Protokoll."
            else:
                status = "succeeded"
    except Exception as exception:
        error = f"{type(exception).__name__}: {exception}"
        status = "failed"
        if process:
            terminate(process)
    finally:
        with store.transaction() as db:
            db.execute("UPDATE runs SET status=?,finished=?,exit_code=?,session_id=?,error=? WHERE id=? AND status='running'",
                       (status, time.time(), return_code, session_id, error, run_id))
            if session_id and job["context_mode"] == "continue":
                row = db.execute("SELECT config FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if row:
                    current = json.loads(row["config"])
                    if current["context_mode"] == "continue" and current["session_id"] == job["session_id"]:
                        current["session_id"] = session_id
                        db.execute("UPDATE jobs SET config=?,updated=?,revision=revision+1 WHERE id=?",
                                   (json.dumps(current, ensure_ascii=False), time.time(), job["id"]))
        lock.close()
    notify(job, status)


def worker_alive(store, run):
    # A lock also detects stale/recycled PIDs after a reboot.
    path = store.root / "runs" / run["id"] / "worker.lock"
    if not path.exists():
        return bool(run["started"] and time.time() - run["started"] < 15)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def tick(store):
    now = time.time()
    with store.transaction() as db:
        for row in db.execute("SELECT * FROM jobs WHERE enabled=1 AND deleted=0 AND next_run<=?", (now,)).fetchall():
            job = store.decode(row)
            store.enqueue(job["id"], "scheduled", db, row["next_run"])
            due = next_time(job["schedule"], job["timezone"], now)
            db.execute("UPDATE jobs SET next_run=?,enabled=? WHERE id=?", (due, int(due is not None), job["id"]))
        active = db.execute("SELECT * FROM runs WHERE status='running'").fetchall()
        for row in active:
            if not worker_alive(store, row):
                db.execute("UPDATE runs SET status='interrupted',finished=?,error=? WHERE id=?",
                           (now, "Worker wurde beendet; Lauf wird nicht automatisch doppelt gestartet.", row["id"]))
        if db.execute("SELECT count(*) FROM runs WHERE status='running'").fetchone()[0] >= 1:
            return None
        row = db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY requested LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE runs SET status='running',started=? WHERE id=?", (now, row["id"]))
        return row["id"]


def daemon(store):
    import faulthandler
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1)
    print(f"Schedule-Dienst gestartet: PID {os.getpid()}", flush=True)
    lock = (store.root / "daemon.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Schedule-Dienst läuft bereits.")
    stopping = [False]
    def stop(_signum, _frame):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    try:
        while not stopping[0]:
            store.state("daemon", {"heartbeat": time.time(), "pid": os.getpid()})
            try:
                run_id = tick(store)
                if run_id:
                    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("main.py")), "worker", run_id],
                                             env=clean_environment(), start_new_session=True)
                    children.append(child)
                    with store.transaction() as db:
                        db.execute("UPDATE runs SET worker_pid=? WHERE id=?", (child.pid, run_id))
                children = [child for child in children if child.poll() is None]
                publish(store)
            except Exception as error:
                print(f"Scheduler: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            for _ in range(10):
                if stopping[0]:
                    break
                time.sleep(0.2)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
        store.state("daemon", {"heartbeat": 0, "pid": None})
        lock.close()
