"""Local MCP stdio interface. Shares exactly the UI's store and scheduler."""
import json
import sys

from core import Store, preview


SCHEDULE = {
    "type": "object", "required": ["type"], "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": ["interval", "daily", "weekly", "cron", "once"]},
        "minutes": {"type": "number", "minimum": 1},
        "time": {"type": "string", "description": "Local HH:MM, e.g. 09:15"},
        "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6},
                 "description": "Weekly: 0=Monday through 6=Sunday"},
        "expression": {"type": "string", "description": "Standard five-field cron expression"},
        "at": {"type": "string", "description": "One-time ISO datetime; explicit UTC offset recommended"},
    },
}
PROPERTIES = {
    "name": {"type": "string"}, "prompt": {"type": "string"},
    "cwd": {"type": "string", "description": "Existing absolute project directory on this Mac"},
    "schedule": SCHEDULE, "timezone": {"type": "string", "default": "Europe/Berlin"},
    "model": {"type": ["string", "null"], "description": "Null uses current Codex CLI default"},
    "reasoning_effort": {"type": ["string", "null"], "enum": [None, "low", "medium", "high", "xhigh", "max"]},
    "sandbox": {"type": "string", "enum": ["read-only", "workspace-write"], "default": "workspace-write"},
    "context_mode": {"type": "string", "enum": ["fresh", "continue"], "default": "fresh",
                     "description": "continue keeps a job-owned Codex conversation across runs"},
    "session_id": {"type": ["string", "null"], "description": "Optional existing Codex UUID for continue mode"},
    "timeout_minutes": {"type": "number", "minimum": 1, "maximum": 1440, "default": 60},
    "enabled": {"type": "boolean", "default": True},
}


def definition(name, description, properties=None, required=None, readonly=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": required or [], "additionalProperties": False},
            "annotations": {"readOnlyHint": readonly, "openWorldHint": False,
                            "destructiveHint": name in ("schedules_delete", "schedules_cancel_run"),
                            "idempotentHint": readonly or name in ("schedules_pause", "schedules_resume", "schedules_delete")}}


IDENTIFIER = {"type": "string"}
TOOLS = [
    definition("schedules_list", "List independent local schedules; includes saved prompt, status and next run.",
               {"include_deleted": {"type": "boolean", "default": False}}, readonly=True),
    definition("schedules_get", "Read a schedule before changing it. Preserve fields the user did not ask to change.",
               {"id": IDENTIFIER}, ["id"], True),
    definition("schedules_preview", "Validate a schedule and preview local next-run times before saving.",
               {"schedule": SCHEDULE, "timezone": {"type": "string", "default": "Europe/Berlin"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}}, ["schedule"], True),
    definition("schedules_create", "Create a future local schedule. Enabled defaults true. Reuse request_key on retries to avoid duplicates.",
               {**PROPERTIES, "request_key": {"type": "string"}}, ["name", "prompt", "cwd", "schedule"]),
    definition("schedules_update", "Change only requested fields. Use expected_revision from get to avoid overwriting another edit; then read back.",
               {"id": IDENTIFIER, "changes": {"type": "object", "properties": PROPERTIES, "additionalProperties": False},
                "expected_revision": {"type": "integer"}}, ["id", "changes"]),
    definition("schedules_pause", "Pause future automatic runs. A currently running task is not cancelled.", {"id": IDENTIFIER}, ["id"]),
    definition("schedules_resume", "Enable future automatic runs; next due time is computed from now.", {"id": IDENTIFIER}, ["id"]),
    definition("schedules_delete", "Move a schedule to the recoverable archive and cancel queued runs. Does not erase logs.", {"id": IDENTIFIER}, ["id"]),
    definition("schedules_restore", "Restore an archived schedule in paused state.", {"id": IDENTIFIER}, ["id"]),
    definition("schedules_run_now", "Explicitly queue one immediate run. Also works for paused jobs; overlapping runs are deduplicated.", {"id": IDENTIFIER}, ["id"]),
    definition("schedules_runs", "List recent execution states and run IDs, optionally for a single schedule.",
               {"id": IDENTIFIER, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, readonly=True),
    definition("schedules_run_result", "Read actual run status, answer, errors, and event log. A queued run is not proof of success.",
               {"run_id": IDENTIFIER, "max_chars": {"type": "integer", "minimum": 100, "maximum": 100000}}, ["run_id"], True),
    definition("schedules_cancel_run", "Cancel a queued or running execution without deleting its schedule.", {"run_id": IDENTIFIER}, ["run_id"]),
    definition("schedules_health", "Read heartbeat of the independent local background scheduler.", readonly=True),
]


def call(store, name, arguments):
    definition = next((tool for tool in TOOLS if tool["name"] == name), None)
    if definition is None:
        raise ValueError("Unbekanntes Werkzeug.")
    if not isinstance(arguments, dict) or set(arguments) - set(definition["inputSchema"]["properties"]):
        raise ValueError("Unbekannte Werkzeugargumente.")
    if set(definition["inputSchema"]["required"]) - set(arguments):
        raise ValueError("Pflichtargumente fehlen.")
    if name == "schedules_list":
        return {"jobs": store.list(arguments.get("include_deleted", False)), "scheduler": store.health()}
    if name == "schedules_get":
        return store.get(arguments["id"])
    if name == "schedules_preview":
        return {"next_runs": preview(arguments["schedule"], arguments.get("timezone", "Europe/Berlin"), arguments.get("count", 5))}
    if name == "schedules_create":
        return store.create({key: value for key, value in arguments.items() if key != "request_key"}, arguments.get("request_key"))
    if name == "schedules_update":
        return store.update(arguments["id"], arguments["changes"], arguments.get("expected_revision"))
    if name in ("schedules_pause", "schedules_resume"):
        return store.update(arguments["id"], {"enabled": name == "schedules_resume"})
    if name in ("schedules_delete", "schedules_restore"):
        return store.archive(arguments["id"], restore=name == "schedules_restore")
    if name == "schedules_run_now":
        return {**store.enqueue(arguments["id"]), "scheduler": store.health()}
    if name == "schedules_runs":
        return store.runs(arguments.get("id"), arguments.get("limit", 30))
    if name == "schedules_run_result":
        return store.result(arguments["run_id"], arguments.get("max_chars", 16000))
    if name == "schedules_cancel_run":
        return store.cancel(arguments["run_id"])
    return store.health()


def serve(store):
    instructions = (
        "Manage independent local schedules here, not ChatGPT app automations. Both the Herdr UI and these tools share one store. "
        "Use Europe/Berlin unless the user specifies another timezone. Read before updating; preserve unrelated fields; preview times and read back changes. "
        "Create only jobs the user requested. A queued run is not a successful result. Mac and launchd scheduler must be running. "
        "Respect the user's authorization for any external communications in scheduled prompts."
    )
    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or "method" not in message:
                raise ValueError("Invalid JSON-RPC request")
            if "id" not in message:
                continue
            method, params = message["method"], message.get("params") or {}
            if method == "initialize":
                version = params.get("protocolVersion", "2024-11-05")
                if version not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
                    version = "2024-11-05"
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "herdr-schedules", "version": "1.0.0"}, "instructions": instructions}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    value = call(store, params["name"], params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
                except Exception as error:
                    result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            else:
                reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
                print(json.dumps(reply), flush=True)
                continue
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as error:
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32700, "message": str(error)}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)
