"""Who is using which speech engine: the bookkeeping behind the engine host, with time passed in as `clock`."""
import hashlib
import json
from dataclasses import dataclass, field

GRACE_S = 120.0
HOST_IDLE_S = 10.0


@dataclass(frozen=True)
class Key:
    """Two servers share an engine process only when every part of this is equal; `connection` is set only for an engine
    that is not shared."""
    kind: str
    name: str
    settings: str
    env: str
    connection: int | None

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


def make_key(kind: str, name: str, settings: dict, env: dict, connection: int | None = None) -> Key:
    digest = hashlib.sha256(json.dumps(sorted(env.items())).encode("utf-8")).hexdigest()[:16]
    return Key(kind, name, json.dumps(settings, sort_keys=True, separators=(",", ":")), digest, connection)


@dataclass
class Attached:
    key: Key
    owner: dict


@dataclass
class Registry:
    clock: object
    grace: float = GRACE_S
    host_idle: float = HOST_IDLE_S
    owners: dict[object, Attached] = field(default_factory=dict)
    idle_since: dict[Key, float] = field(default_factory=dict)

    def __post_init__(self):
        self.empty_since: float | None = self.clock()

    def attach(self, conn: object, key: Key, owner: dict) -> None:
        if conn in self.owners:
            raise ValueError("this connection is already attached")
        self.owners[conn] = Attached(key, owner)
        self.idle_since.pop(key, None)
        self.empty_since = None

    def detach(self, conn: object) -> None:
        attached = self.owners.pop(conn, None)
        if attached is not None and not self.users(attached.key):
            self.idle_since[attached.key] = self.clock()

    def forget(self, conn: object) -> None:
        """Drops a connection with no grace for its engine: the engine was never used, or was not shared."""
        self.owners.pop(conn, None)
        self.note_activity()

    def reap(self) -> list[Key]:
        """The keys whose last owner went at least the grace period ago; they are no longer known."""
        now = self.clock()
        due = [key for key, since in self.idle_since.items() if now - since >= self.grace]
        for key in due:
            del self.idle_since[key]
        if due and not self.keys():
            self.empty_since = now
        return due

    def users(self, key: Key) -> list[object]:
        return [conn for conn, attached in self.owners.items() if attached.key == key]

    def keys(self) -> set[Key]:
        return {attached.key for attached in self.owners.values()} | set(self.idle_since)

    def note_activity(self) -> None:
        """A start that came to nothing still counts as the host having been wanted just now."""
        if not self.keys():
            self.empty_since = self.clock()

    def host_done(self) -> bool:
        return self.empty_since is not None and self.clock() - self.empty_since >= self.host_idle

    def grace_left(self, key: Key) -> float | None:
        since = self.idle_since.get(key)
        return None if since is None else max(0.0, self.grace - (self.clock() - since))
