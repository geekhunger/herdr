#!/usr/bin/env python3
"""Herdr Schedules: local UI, MCP server, scheduler, worker, and JSON CLI."""
import argparse
import json
import os
import sys

os.umask(0o077)
from core import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["ui", "mcp", "daemon", "worker", "call"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--json", dest="payload", default=None, help="JSON arguments; otherwise read stdin")
    args = parser.parse_args()
    if args.mode in ("mcp", "call"):
        for stream in (sys.stdin, sys.stdout):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    store = Store()
    if args.mode == "ui":
        from tui import start
        start(store)
    elif args.mode == "mcp":
        from mcp_server import serve
        serve(store)
    elif args.mode == "daemon":
        from runner import daemon
        daemon(store)
    elif args.mode == "worker":
        from runner import worker
        worker(store, args.argument)
    elif args.mode == "call":
        from mcp_server import call
        arguments = json.loads(args.payload if args.payload is not None else sys.stdin.read() or "{}")
        print(json.dumps(call(store, args.argument, arguments), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
