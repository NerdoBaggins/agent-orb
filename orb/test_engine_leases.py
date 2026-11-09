import pytest

from orb.engine_leases import Registry, make_key

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
    return Registry(clock, lease_ttl=20, grace=30, host_idle=10)


def key(settings=None, env=None, voice=None, kind="stt", name="fake"):
    return make_key(kind, name, settings or {}, env or {}, voice)


# ---- keying ----

def test_the_order_of_the_load_settings_does_not_split_a_key():
    assert key({"a": 1, "b": 2}) == key({"b": 2, "a": 1})


@pytest.mark.parametrize("other", [
    key({"text": "x"}),
    key(env={"API_KEY": "1"}),
    key(voice="af_sky"),
    key(name="other"),
    key(kind="tts"),
])
def test_a_difference_in_what_load_receives_or_in_who_may_use_it_is_a_different_key(other):
    assert other != key()


def test_two_credentials_with_different_values_never_share_a_key_and_the_key_does_not_hold_either():
    one, two = key(env={"API_KEY": "secret-one"}), key(env={"API_KEY": "secret-two"})
    assert one != two and "secret-one" not in repr(one)


# ---- leases and grace ----

def test_a_key_stays_while_any_lease_on_it_remains(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.attach("b", k, OWNER)
    registry.detach("a")
    clock.advance(1000)
    assert registry.reap() == [] and registry.users(k) == ["b"]


def test_the_last_lease_going_starts_the_grace_and_the_key_is_reaped_only_when_it_has_run_out(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    clock.advance(29.9)
    assert registry.reap() == []
    clock.advance(0.1)
    assert registry.reap() == [k]


def test_attaching_during_the_grace_keeps_the_engine(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    clock.advance(20)
    registry.attach("b", k, OWNER)
    clock.advance(1000)
    assert registry.reap() == [] and registry.grace_left(k) is None


def test_a_reaped_key_is_forgotten_so_it_is_reaped_once(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(30)
    assert len(registry.reap()) == 1 and registry.reap() == [] and registry.keys() == set()


def test_the_grace_left_counts_down_for_an_idle_key(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    registry.detach("a")
    clock.advance(10)
    assert registry.grace_left(k) == 20


def test_two_keys_are_reaped_independently(registry, clock):
    one, two = key(name="one"), key(name="two")
    registry.attach("a", one, OWNER)
    registry.attach("b", two, OWNER)
    registry.detach("a")
    clock.advance(30)
    assert registry.reap() == [one] and registry.users(two) == ["b"]


def test_a_lease_id_cannot_be_held_twice(registry):
    registry.attach("a", key(), OWNER)
    with pytest.raises(ValueError):
        registry.attach("a", key(name="other"), OWNER)


# ---- heartbeats ----

def test_a_lease_without_a_heartbeat_lapses_only_after_the_ttl(registry, clock):
    registry.attach("a", key(), OWNER)
    clock.advance(20)
    assert registry.expire() == []
    clock.advance(0.1)
    assert registry.expire() == ["a"]


def test_a_heartbeat_keeps_a_lease_alive(registry, clock):
    registry.attach("a", key(), OWNER)
    for _ in range(5):
        clock.advance(15)
        assert registry.touch("a") is True
        assert registry.expire() == []


def test_a_lapsed_lease_is_gone_and_its_heartbeat_says_so(registry, clock):
    registry.attach("a", key(), OWNER)
    clock.advance(21)
    registry.expire()
    assert registry.touch("a") is False


def test_a_lapsed_last_lease_starts_the_grace_so_the_engine_stops(registry, clock):
    k = key()
    registry.attach("a", k, OWNER)
    clock.advance(21)
    registry.expire()
    clock.advance(30)
    assert registry.reap() == [k]


def test_a_lease_in_the_middle_of_a_call_does_not_lapse(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.begin_call("a")
    clock.advance(500)
    assert registry.expire() == []


def test_the_ttl_runs_from_the_end_of_the_call(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.begin_call("a")
    clock.advance(500)
    registry.end_call("a")
    clock.advance(19)
    assert registry.expire() == []
    clock.advance(2)
    assert registry.expire() == ["a"]


def test_a_call_on_a_lease_that_is_gone_is_refused(registry):
    assert registry.begin_call("nobody") is False


# ---- the host's own exit ----

def test_a_host_nobody_attached_to_is_done_after_the_idle_time(registry, clock):
    clock.advance(9.9)
    assert registry.host_done() is False
    clock.advance(0.1)
    assert registry.host_done() is True


def test_a_host_with_a_lease_is_not_done(registry, clock):
    registry.attach("a", key(), OWNER)
    clock.advance(1000)
    assert registry.host_done() is False


def test_a_host_with_an_engine_in_grace_is_not_done(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(29)
    assert registry.host_done() is False


def test_a_host_is_done_once_the_last_engine_is_reaped_and_the_idle_time_has_passed(registry, clock):
    registry.attach("a", key(), OWNER)
    registry.detach("a")
    clock.advance(30)
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
