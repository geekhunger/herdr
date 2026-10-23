"""Bounded, atomic presentation snapshot for the local Herdr client."""
import datetime as dt
import json
import os
import time


def publish(store):
    jobs = store.list()
    priority = {"running": 0, "queued": 1, "failed": 2, "timed_out": 2, "interrupted": 2}
    jobs.sort(key=lambda job: (
        priority.get((job.get("last_run") or {}).get("status"), 3 if job["enabled"] else 4),
        job["next_run_ts"] or float("inf"), job["name"].casefold()))
    rows = []
    for job in jobs[:200]:
        status = (job.get("last_run") or {}).get("status", "never")
        due = job["next_run_at"]
        if due:
            due = dt.datetime.fromisoformat(due).strftime("%d.%m. %H:%M")
        rows.append({"name": " ".join(job["name"].split())[:100], "enabled": job["enabled"],
                     "status": status, "next": due or "pausiert"})
    data = {"generated_at": time.time(), "total": len(jobs),
            "active": sum(job["enabled"] for job in jobs), "jobs": rows}
    temporary = store.root / "sidebar.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, store.root / "sidebar.json")
