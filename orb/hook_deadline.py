import faulthandler
import os
import sys
import threading
from contextlib import contextmanager

DEADLINE_S = 10
GRACE_S = 5


@contextmanager
def deadline(seconds: float | None = None, grace: float | None = None):
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
