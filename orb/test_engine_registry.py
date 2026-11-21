import pytest

from orb.engine_registry import Registry, make_key

OWNER = {"pid": 1, "repo": "/a", "agent": "claude"}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def registry(clock):
    return Registry(clock, grace=120, host_idle=10)


def key(settings=None, env=None, kind="stt", name="fake", connection=None):
    return make_key(kind, name, settings or {}, env or {}, connection)


def test_the_key_ignores_setting_order_and_splits_on_kind_name_settings_env_and_connection():
    base = key({"a": 1, "b": 2})
    splits = [key({"a": 1, "b": 3}), key({"a": 1, "b": 2}, kind="tts"), key({"a": 1, "b": 2}, name="other"),
              key({"a": 1, "b": 2}, env={"API_KEY": "1"}), key({"a": 1, "b": 2}, connection=1), key({"a": 1, "b": 2}, connection=2)]
    assert key({"b": 2, "a": 1}) == base and base not in splits and len(set(splits)) == len(splits)


def test_two_credentials_with_different_values_never_share_a_key_and_the_key_does_not_hold_either():
    one, two = key(env={"API_KEY": "secret-one"}), key(env={"API_KEY": "secret-two"})
    assert one != two and "secret-one" not in repr(one)


def test_a_key_stays_while_any_owner_remains(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.attach("b", k, OWNER)
    registry.detach("a")
    clock.advance(1000)
    assert registry.reap() == [] and registry.users(k) == ["b"]


def test_the_last_owner_going_starts_the_grace_and_the_key_is_reaped_only_when_it_has_run_out(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    clock.advance(119.9)
    assert registry.reap() == []
    clock.advance(0.1)
    assert registry.reap() == [k]


def test_a_reaped_key_is_forgotten_so_it_is_reaped_once(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(120)
    assert len(registry.reap()) == 1 and registry.reap() == [] and registry.keys() == set()


def test_the_grace_left_counts_down_for_an_idle_key(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    clock.advance(10)
    assert registry.grace_left(k) == 110


def test_two_keys_are_reaped_independently(registry, clock):
    one, two = key(name="one"), key(name="two")
    registry.attach("a", one, OWNER)
    registry.attach("b", two, OWNER)
    registry.detach("a")
    clock.advance(120)
    assert registry.reap() == [one] and registry.users(two) == ["b"]


def test_a_connection_cannot_be_attached_twice(registry):
    registry.attach("a", key(), OWNER)
    with pytest.raises(ValueError):
        registry.attach("a", key(name="other"), OWNER)


def test_a_host_nobody_attached_to_is_done_after_the_idle_time(registry, clock):
    clock.advance(9.9)
    assert registry.host_done() is False
    clock.advance(0.1)
    assert registry.host_done() is True


def test_a_host_with_an_owner_is_not_done(registry, clock):
    registry.attach("a", key(), OWNER)
    clock.advance(1000)
    assert registry.host_done() is False


def test_a_host_with_an_engine_in_grace_is_not_done(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(119)
    assert registry.host_done() is False


def test_a_host_is_done_once_the_last_engine_is_reaped_and_the_idle_time_has_passed(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(120)
    registry.reap()
    clock.advance(9.9)
    assert registry.host_done() is False
    clock.advance(0.1)
    assert registry.host_done() is True


def test_a_start_that_came_to_nothing_restarts_the_hosts_idle_time(registry, clock):
    clock.advance(9)
    registry.note_activity()
    clock.advance(9)
    assert registry.host_done() is False


def test_note_activity_leaves_a_host_with_engines_alone(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.note_activity()
    clock.advance(1000)
    assert registry.host_done() is False


def test_the_last_connection_closing_starts_a_grace_and_an_attach_within_it_keeps_the_engine(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    assert registry.grace_left(k) == 120
    clock.advance(119)
    registry.attach("b", k, OWNER)
    clock.advance(1000)
    assert registry.reap() == [] and registry.users(k) == ["b"]


def test_forgetting_a_connection_starts_no_grace_and_lets_the_host_count_as_idle(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.forget("a")
    assert registry.grace_left(k) is None and registry.keys() == set()
    clock.advance(10)
    assert registry.host_done() is True
