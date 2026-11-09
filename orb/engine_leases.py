"""Who is using which speech engine: the bookkeeping behind the engine host, with time passed in as `clock`."""
import hashlib
import json
from dataclasses import dataclass, field

HEARTBEAT_S = 5.0
LEASE_TTL_S = 20.0
GRACE_S = 30.0
HOST_IDLE_S = 10.0


@dataclass(frozen=True)
class Key:
    """Two servers share an engine process only when every part of this is equal."""
    kind: str
    name: str
    settings: str
    env: str
    voice: str | None

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}" + (f" ({self.voice})" if self.voice else "")


def make_key(kind: str, name: str, settings: dict, env: dict, voice: str | None) -> Key:
    digest = hashlib.sha256(json.dumps(sorted(env.items())).encode("utf-8")).hexdigest()[:16]
    return Key(kind, name, json.dumps(settings, sort_keys=True), digest, voice)


@dataclass
class Lease:
    key: Key
    owner: dict
    seen: float
    calls: int = 0


@dataclass
class Registry:
    clock: object
    lease_ttl: float = LEASE_TTL_S
    grace: float = GRACE_S
    host_idle: float = HOST_IDLE_S
    leases: dict[str, Lease] = field(default_factory=dict)
    idle_since: dict[Key, float] = field(default_factory=dict)

    def __post_init__(self):
        self.empty_since: float | None = self.clock()

    def attach(self, lease_id: str, key: Key, owner: dict) -> None:
        if lease_id in self.leases:
            raise ValueError(f"lease {lease_id} is already held")
        self.leases[lease_id] = Lease(key, owner, self.clock())
        self.idle_since.pop(key, None)
        self.empty_since = None

    def touch(self, lease_id: str) -> bool:
        """False when the lease is gone: it expired or was detached."""
        lease = self.leases.get(lease_id)
        if lease is None:
            return False
        lease.seen = self.clock()
        return True

    def begin_call(self, lease_id: str) -> bool:
        lease = self.leases.get(lease_id)
        if lease is None:
            return False
        lease.calls += 1
        return True

    def end_call(self, lease_id: str) -> None:
        lease = self.leases.get(lease_id)
        if lease is not None:
            lease.calls -= 1
            lease.seen = self.clock()

    def detach(self, lease_id: str) -> None:
        lease = self.leases.pop(lease_id, None)
        if lease is not None and not self.users(lease.key):
            self.idle_since[lease.key] = self.clock()

    def expire(self) -> list[str]:
        """Drops the leases whose server has not been heard from within the TTL and is not in the middle of a call."""
        now = self.clock()
        gone = [i for i, lease in self.leases.items() if lease.calls == 0 and now - lease.seen > self.lease_ttl]
        for lease_id in gone:
            self.detach(lease_id)
        return gone

    def reap(self) -> list[Key]:
        """The keys whose last lease went at least the grace period ago; they are no longer known."""
        now = self.clock()
        due = [key for key, since in self.idle_since.items() if now - since >= self.grace]
        for key in due:
            del self.idle_since[key]
        if due and not self.keys():
            self.empty_since = now
        return due

    def users(self, key: Key) -> list[str]:
        return [i for i, lease in self.leases.items() if lease.key == key]

    def keys(self) -> set[Key]:
        return {lease.key for lease in self.leases.values()} | set(self.idle_since)

    def note_activity(self) -> None:
        """A start that came to nothing still counts as the host having been wanted just now."""
        if not self.keys():
            self.empty_since = self.clock()

    def host_done(self) -> bool:
        return self.empty_since is not None and self.clock() - self.empty_since >= self.host_idle

    def grace_left(self, key: Key) -> float | None:
        since = self.idle_since.get(key)
        return None if since is None else max(0.0, self.grace - (self.clock() - since))
