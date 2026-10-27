"""Hook for the events that open or close a turn, in Claude Code and in Copilot: records which, so the orb server knows
whether the session is busy without reading its log. Stdlib only; run with `uv run --no-project`.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb.state import hook_agent, project_root, record_project, record_turn  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    project = project_root(payload, os.environ) if isinstance(payload, dict) else None
    if project:
        for write in (lambda: record_turn(payload, project, Path.home(), datetime.now(timezone.utc), hook_agent(os.environ)),
                      lambda: record_project(payload, project, Path.home())):
            try:
                write()
            except Exception:
                # A hook that fails loudly blocks nothing useful and could stall the session's prompt or turn end.
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
