"""Claude Code `SessionStart` hook: records where this session's transcript is, so the orb server can show the
session's `/rename` name from the moment it attaches. Stdlib only; run with `uv run --no-project`.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb.state import hook_agent, project_root, record_session  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    project = project_root(payload, os.environ) if isinstance(payload, dict) else None
    if project and hook_agent(os.environ) == "claude":
        try:
            record_session(payload, project, Path.home())
        except Exception:
            # A hook that fails loudly blocks nothing useful and could stall the session's start.
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
