"""The orb server's log: one per repo and agent in its state folder, always on, rotating at MAX_BYTES through BACKUPS
older files so the history stays bounded (about 5 MB) and the newest is always there to read."""
import logging
import logging.handlers
from pathlib import Path

MAX_BYTES = 1_000_000
BACKUPS = 4
FORMAT = "%(asctime)s %(levelname)s %(message)s"


def log_path(state: Path, agent: str) -> Path:
    return Path(state) / f"server.{agent}.log"


def output_path(state: Path, agent: str) -> Path:
    """The server process's own stdout and stderr: a crash before logging starts. Started afresh by each `orb open`,
    so it holds one run."""
    return Path(state) / f"server.{agent}.out"


def setup(path: Path, max_bytes: int = MAX_BYTES, backups: int = BACKUPS) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def tail(path: Path, lines: int, backups: int = BACKUPS) -> list[str]:
    """The last `lines` lines across the log and its rotated files, oldest first."""
    out: list[str] = []
    for file in [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, backups + 1))]:
        if len(out) >= lines:
            break
        try:
            out = file.read_text(encoding="utf-8", errors="replace").splitlines() + out
        except OSError:
            continue
    return out[-lines:] if lines else []
