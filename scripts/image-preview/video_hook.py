#!/usr/bin/env python3
"""Resolve video badges through Codex's normal, reviewable hook interface."""
import json
import os
import sys
from video_attachments import initialize, prompt_context


def main():
    if os.environ.get('HERDR_ENV') != '1' or not os.environ.get('HERDR_DRAFT_EDITOR_TOKEN'):
        return
    try:
        event = json.loads(sys.stdin.read(2 * 1024 * 1024))
        if event.get('hook_event_name') == 'SessionStart':
            initialize(event)
            return
        if event.get('hook_event_name') == 'UserPromptSubmit':
            result = prompt_context(event)
            if result:
                print(json.dumps(result))
    except (KeyError, OSError, ValueError) as error:
        if 'event' in locals() and event.get('hook_event_name') == 'UserPromptSubmit' and '[Video #' in event.get('prompt', ''):
            print(json.dumps({'decision': 'block', 'reason': 'Video attachment lookup failed: ' + str(error)}))
        else:
            print('Herdr video attachment setup: ' + str(error), file=sys.stderr)


if __name__ == '__main__':
    main()
