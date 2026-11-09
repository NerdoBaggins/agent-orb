"""A deadline for a plugin hook. Hooks run synchronously at every prompt and turn end, so one that stalls stalls the
session, and a hook Claude Code gives up on can be orphaned under `uv run`. Stdlib only."""
import faulthandler
import os
import sys
import threading
from contextlib import contextmanager

DEADLINE_S = 10
# Past the deadline, faulthandler's watchdog (a C thread) ends a hook the timer could not: the timer needs the GIL.
GRACE_S = 5


@contextmanager
def deadline(seconds: float | None = None, grace: float | None = None):
    """Ends the process with exit 0 after `seconds`, the way a hook's own error paths do, since a hook never blocks
    anything. If a long C call keeps the timer from running, exits 1 `grace` seconds later with every thread's
    traceback on stderr. Leaving the block disarms both."""
    seconds = DEADLINE_S if seconds is None else seconds
    grace = GRACE_S if grace is None else grace
    timer = threading.Timer(seconds, os._exit, (0,))
    timer.daemon = True
    timer.start()
    faulthandler.dump_traceback_later(seconds + grace, exit=True, file=sys.stderr)
    try:
        yield
    finally:
        timer.cancel()
        faulthandler.cancel_dump_traceback_later()
