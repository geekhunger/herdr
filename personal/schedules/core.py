"""Shared schedule engine and transactional storage. No ChatGPT app dependency."""
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

from croniter import croniter

DEFAULT_HOME = Path.home() / ".local/share/herdr-schedules"
FIELDS = {"name", "prompt", "cwd", "schedule", "timezone", "model", "reasoning_effort",
          "sandbox", "context_mode", "session_id", "timeout_minutes", "enabled"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def home():
    return Path(os.environ.get("HERDR_SCHEDULES_HOME", str(DEFAULT_HOME))).expanduser()


def zoned(timestamp, timezone="Europe/Berlin"):
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).isoformat(timespec="seconds")


def normalize_schedule(value):
    if not isinstance(value, dict):
        raise ValueError("Schedule must be an object.")
    kind = value.get("type")
    allowed = {"interval": {"type", "minutes"}, "daily": {"type", "time"},
               "weekly": {"type", "time", "days"}, "cron": {"type", "expression"},
               "once": {"type", "at"}}
    if kind not in allowed or set(value) - allowed.get(kind, set()):
        raise ValueError("Schedule: interval, daily, weekly, cron or once; unknown fields.")
    result = dict(value)
    if kind == "interval":
        minutes = value.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes < 1:
            raise ValueError("Interval must be at least one minute.")
    if kind in ("daily", "weekly"):
        clock = value.get("time", "")
        if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
            raise ValueError("Enter time as HH:MM.")
    if kind == "weekly":
        days = value.get("days")
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise ValueError("Weekdays: list from 0 (Monday) to 6 (Sunday).")
        result["days"] = sorted(set(days))
    if kind == "cron":
        expression = value.get("expression", "")
        if not isinstance(expression, str) or len(expression.split()) != 5 or not croniter.is_valid(expression):
            raise ValueError("Cron expression requires five valid fields: minute hour day month weekday.")
    if kind == "once":
        try:
            dt.datetime.fromisoformat(value["at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("Enter a one-time ISO datetime, e.g. 2026-11-01T09:00:00+01:00.")
    return result


def next_time(schedule, timezone, after):
    schedule = normalize_schedule(schedule)
    zone = ZoneInfo(timezone)
    kind = schedule["type"]
    if kind == "interval":
        return after + float(schedule["minutes"]) * 60
    if kind == "once":
        date = dt.datetime.fromisoformat(schedule["at"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=zone)
            if dt.datetime.fromtimestamp(date.timestamp(), zone).replace(tzinfo=None) != date.replace(tzinfo=None):
                raise ValueError("This local time does not exist because of daylight saving time.")
        return date.timestamp() if date.timestamp() > after else None
    if kind == "cron":
        expression = schedule["expression"]
    else:
        hour, minute = map(int, schedule["time"].split(":"))
        days = "*" if kind == "daily" else ",".join(DAYS[d] for d in schedule["days"])
        expression = f"{minute} {hour} * * {days}"
    # Iterate wall-clock times, then localize: skip missing DST times, run ambiguous
    # times once (the first occurrence). Never return an instant in the past.
    local = dt.datetime.fromtimestamp(after, zone).replace(tzinfo=None)
    iterator = croniter(expression, local, max_years_between_matches=8)
    for _ in range(32):
        candidate = iterator.get_next(dt.datetime)
        aware = candidate.replace(tzinfo=zone, fold=0)
        timestamp = aware.timestamp()
        if timestamp > after and dt.datetime.fromtimestamp(timestamp, zone).replace(tzinfo=None) == candidate:
            return timestamp
    raise ValueError("No valid next run found.")


def preview(schedule, timezone="Europe/Berlin", count=5, after=None):
    now = time.time() if after is None else after
    result = []
    for _ in range(max(1, min(int(count), 20))):
        now = next_time(schedule, timezone, now)
        if now is None:
            break
        result.append(zoned(now, timezone))
    return result


def schedule_text(schedule):
    kind = schedule["type"]
    if kind == "interval":
        return f"every {schedule['minutes']:g}m"
    if kind == "daily":
        return "daily " + schedule["time"]
    if kind == "weekly":
        return "weekly " + ",".join(DAYS[d] for d in schedule["days"]) + " " + schedule["time"]
    return kind + " " + schedule["expression" if kind == "cron" else "at"]


def schedule_from_text(text):
    text = text.strip()
    match = re.fullmatch(r"(?:every|alle)\s+(\d+(?:\.\d+)?)\s*([mhd])", text, re.I)
    if match:
        minutes = float(match[1]) * {"m": 1, "h": 60, "d": 1440}[match[2].lower()]
        return normalize_schedule({"type": "interval", "minutes": minutes})
    parts = text.split()
    if len(parts) == 2 and parts[0].lower() in ("daily", "täglich"):
        return normalize_schedule({"type": "daily", "time": parts[1]})
    if len(parts) == 3 and parts[0].lower() in ("weekly", "wöchentlich"):
        aliases = {name: i for i, name in enumerate(DAYS)}
        aliases.update({name: i for i, name in enumerate(["mo", "di", "mi", "do", "fr", "sa", "so"])})
        try:
            days = [aliases[d.lower()] for d in parts[1].split(",")]
        except KeyError:
            raise ValueError("Enter weekdays, e.g. mon,wed,fri.")
        return normalize_schedule({"type": "weekly", "days": days, "time": parts[2]})
    if text.startswith("cron "):
        return normalize_schedule({"type": "cron", "expression": text[5:].strip()})
    if text.startswith("once "):
        return normalize_schedule({"type": "once", "at": text[5:].strip()})
    raise ValueError("Schedule: every 2h | daily 09:00 | weekly mon,fri 09:00 | cron 0 9 * * 1-5 | once ISO datetime")


def validate(values):
    unknown = set(values) - FIELDS
    if unknown:
        raise ValueError("Unknown fields: " + ", ".join(sorted(unknown)))
    data = {"timezone": "Europe/Berlin", "model": None, "reasoning_effort": None,
            "sandbox": "workspace-write", "context_mode": "fresh", "session_id": None,
            "timeout_minutes": 60, "enabled": True, **values}
    for name in ("name", "prompt", "cwd"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise ValueError(f"{name} must not be empty.")
    if len(data["name"]) > 200 or len(data["prompt"]) > 1000000:
        raise ValueError("Name or prompt is too long.")
    path = Path(data["cwd"]).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Project directory must be an existing absolute path.")
    data["cwd"] = str(path.resolve())
    data["schedule"] = normalize_schedule(data.get("schedule"))
    try:
        ZoneInfo(data["timezone"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid timezone, e.g. Europe/Berlin.")
    if data["sandbox"] not in ("read-only", "workspace-write"):
        raise ValueError("Access: read-only or workspace-write.")
    if data["context_mode"] not in ("fresh", "continue"):
        raise ValueError("Context: fresh or continue.")
    if data["session_id"] and not re.fullmatch(r"[0-9a-fA-F-]{36}", str(data["session_id"])):
        raise ValueError("Session ID must be a Codex UUID.")
    for name in ("model", "reasoning_effort"):
        if data[name] == "":
            data[name] = None
        if data[name] is not None and (not isinstance(data[name], str) or len(data[name]) > 100):
            raise ValueError(f"Invalid field: {name}")
    if data["reasoning_effort"] not in (None, "low", "medium", "high", "xhigh", "max"):
        raise ValueError("Reasoning effort: low, medium, high, xhigh or max (depending on model).")
    if type(data["enabled"]) is not bool:
        raise ValueError("enabled must be true or false.")
    if isinstance(data["timeout_minutes"], bool) or not isinstance(data["timeout_minutes"], (int, float)) or not 1 <= data["timeout_minutes"] <= 1440:
        raise ValueError("Timeout: 1–1440 minutes.")
    return data


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else home()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "schedules.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, enabled INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0, next_run REAL, created REAL NOT NULL,
                    updated REAL NOT NULL, revision INTEGER NOT NULL, request_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                    source TEXT NOT NULL, status TEXT NOT NULL, requested REAL NOT NULL,
                    scheduled_for REAL, started REAL, finished REAL, exit_code INTEGER,
                    session_id TEXT, error TEXT, worker_pid INTEGER, pid INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, outdir TEXT);
                CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id,requested DESC);
                CREATE INDEX IF NOT EXISTS runs_state ON runs(status);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10, factory=Connection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextlib.contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def decode(self, row):
        if row is None:
            raise ValueError("Schedule not found.")
        data = json.loads(row["config"])
        data.update(id=row["id"], enabled=bool(row["enabled"]), deleted=bool(row["deleted"]),
                    revision=row["revision"], created_at=zoned(row["created"], data["timezone"]),
                    updated_at=zoned(row["updated"], data["timezone"]), next_run_ts=row["next_run"],
                    next_run_at=zoned(row["next_run"], data["timezone"]))
        return data

    def get(self, job_id):
        with self.connect() as db:
            data = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            last = db.execute("SELECT id,status,started,finished,error FROM runs WHERE job_id=? ORDER BY requested DESC LIMIT 1", (job_id,)).fetchone()
        data["last_run"] = dict(last) if last else None
        return data

    def list(self, include_deleted=False):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE (? OR deleted=0) ORDER BY created", (int(include_deleted),)).fetchall()
        return [self.get(row["id"]) for row in rows]

    def create(self, values, request_key=None):
        data = validate(values)
        now = time.time()
        next_run = next_time(data["schedule"], data["timezone"], now) if data["enabled"] else None
        if data["enabled"] and next_run is None:
            raise ValueError("The one-time date is in the past.")
        job_id = str(uuid.uuid4())
        with self.transaction() as db:
            if request_key:
                row = db.execute("SELECT id FROM jobs WHERE request_key=?", (request_key,)).fetchone()
                if row:
                    job_id = row["id"]
                    return self.get(job_id)
            db.execute("INSERT INTO jobs(id,config,enabled,next_run,created,updated,revision,request_key) VALUES(?,?,?,?,?,?,1,?)",
                       (job_id, json.dumps(data, ensure_ascii=False), int(data["enabled"]), next_run, now, now, request_key))
        return self.get(job_id)

    def update(self, job_id, changes, expected_revision=None):
        if not isinstance(changes, dict) or not changes or set(changes) - FIELDS:
            raise ValueError("Changes contain no valid schedule fields.")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            current = self.decode(row)
            if current["deleted"]:
                raise ValueError("Schedule is archived. Restore it first.")
            if expected_revision is not None and current["revision"] != expected_revision:
                raise ValueError("Schedule has changed. Reload, then save again.")
            data = validate({**{key: current[key] for key in FIELDS}, **changes})
            due = row["next_run"]
            if any(data[key] != current[key] for key in ("schedule", "timezone", "enabled")):
                due = next_time(data["schedule"], data["timezone"], time.time()) if data["enabled"] else None
                if data["enabled"] and due is None:
                    raise ValueError("The one-time date is in the past.")
            db.execute("UPDATE jobs SET config=?,enabled=?,next_run=?,updated=?,revision=revision+1 WHERE id=?",
                       (json.dumps(data, ensure_ascii=False), int(data["enabled"]), due, time.time(), job_id))
        return self.get(job_id)

    def archive(self, job_id, restore=False):
        with self.transaction() as db:
            self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            db.execute("UPDATE jobs SET deleted=?,enabled=0,next_run=NULL,updated=?,revision=revision+1 WHERE id=?", (int(not restore), time.time(), job_id))
            if not restore:
                db.execute("UPDATE runs SET status='cancelled',finished=? WHERE job_id=? AND status='queued'", (time.time(), job_id))
        return self.get(job_id)

    def enqueue(self, job_id, source="manual", db=None, scheduled_for=None):
        if db is None:
            with self.transaction() as transaction:
                return self.enqueue(job_id, source, transaction, scheduled_for)
        job = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if job["deleted"]:
            raise ValueError("Archived schedules cannot be run.")
        existing = db.execute("SELECT id FROM runs WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone()
        if existing:
            return {"run_id": existing["id"], "already_pending": True}
        run_id = str(uuid.uuid4())
        db.execute("INSERT INTO runs(id,job_id,snapshot,source,status,requested,scheduled_for) VALUES(?,?,?,?,'queued',?,?)",
                   (run_id, job_id, json.dumps(job, ensure_ascii=False), source, time.time(), scheduled_for))
        return {"run_id": run_id, "already_pending": False}

    def runs(self, job_id=None, limit=30):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM runs WHERE (? IS NULL OR job_id=?) ORDER BY requested DESC LIMIT ?", (job_id, job_id, max(1, min(int(limit), 200)))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["snapshot"] = json.loads(item["snapshot"])
            result.append(item)
        return result

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Run not found.")
        result = dict(row)
        result["snapshot"] = json.loads(result["snapshot"])
        return result

    def result(self, run_id, max_chars=16000):
        run = self.run(run_id)
        limit = max(100, min(int(max_chars), 100000))
        for name, filename in (("answer", "answer.txt"), ("stderr", "stderr.log"), ("events", "events.jsonl")):
            path = self.root / "runs" / run_id / filename
            if path.exists():
                with path.open("rb") as file:
                    file.seek(0, 2)
                    size = file.tell()
                    file.seek(max(0, size - limit * 4))
                    run[name] = file.read().decode("utf-8", errors="replace")[-limit:]
            else:
                run[name] = ""
        return run

    def cancel(self, run_id):
        with self.transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Run not found.")
            if row["status"] == "queued":
                db.execute("UPDATE runs SET status='cancelled',finished=? WHERE id=?", (time.time(), run_id))
            elif row["status"] == "running":
                db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
        return self.run(run_id)

    def state(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, json.dumps(value)))
                return value
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def health(self):
        heartbeat = self.state("daemon")
        return {"home": str(self.root), "daemon": heartbeat,
                "healthy": bool(heartbeat and time.time() - heartbeat["heartbeat"] < 20),
                "catch_up": "one run on wake; missed repetitions are coalesced",
                "concurrency": 1}
