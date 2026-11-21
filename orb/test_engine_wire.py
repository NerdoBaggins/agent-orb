import json
import socket
import stat
import sys
import threading
import time

import pytest

from orb import engine_wire
from orb.engine_wire import CopyKind, HostRecord, Link, WireBroken, WireTimeout


def make_copy(root, version="1.0.0", git=None):
    (root / ".claude-plugin").mkdir(parents=True)
    if version is not None:
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    if git == "folder":
        (root / ".git").mkdir()
    elif git == "file":
        (root / ".git").write_text("gitdir: ../elsewhere", encoding="utf-8")
    return root


def test_an_installed_folder_named_for_its_version_is_a_claude_code_copy(tmp_path):
    assert engine_wire.copy_kind(make_copy(tmp_path / "agent-orb" / "1.4.0", "1.4.0")) is CopyKind.CLAUDE_CODE


def test_an_installed_folder_not_named_for_its_version_is_a_copilot_copy(tmp_path):
    assert engine_wire.copy_kind(make_copy(tmp_path / "agent-orb", "1.4.0")) is CopyKind.COPILOT


@pytest.mark.parametrize("git", ["folder", "file"])
def test_a_folder_with_a_git_entry_is_a_checkout_even_when_it_is_named_for_its_version(tmp_path, git):
    assert engine_wire.copy_kind(make_copy(tmp_path / "1.4.0", "1.4.0", git=git)) is CopyKind.CHECKOUT


def test_a_folder_without_a_readable_version_is_a_checkout(tmp_path):
    assert engine_wire.copy_kind(make_copy(tmp_path / "agent-orb", version=None)) is CopyKind.CHECKOUT


@pytest.mark.parametrize("text", ["{", "[]", '{"version": 3}', '{"version": ""}', "{}"])
def test_a_plugin_file_that_gives_no_version_text_is_a_checkout(tmp_path, text):
    root = make_copy(tmp_path / "agent-orb", version=None)
    (root / ".claude-plugin" / "plugin.json").write_text(text, encoding="utf-8")
    assert engine_wire.copy_kind(root) is CopyKind.CHECKOUT


def test_two_claude_code_copies_of_one_version_have_one_identity_wherever_they_sit(tmp_path):
    one, two = make_copy(tmp_path / "a" / "1.4.0", "1.4.0"), make_copy(tmp_path / "b" / "1.4.0", "1.4.0")
    assert engine_wire.identity(one) == engine_wire.identity(two)


def test_two_versions_have_two_identities(tmp_path):
    one, two = make_copy(tmp_path / "1.4.0", "1.4.0"), make_copy(tmp_path / "1.5.0", "1.5.0")
    assert engine_wire.identity(one) != engine_wire.identity(two)


def test_a_copilot_copy_is_keyed_by_its_folder_and_not_by_its_version(tmp_path):
    root = make_copy(tmp_path / "agent-orb", "1.4.0")
    before = engine_wire.identity(root)
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.5.0"}', encoding="utf-8")
    assert engine_wire.identity(root) == before


def test_a_copilot_copy_and_a_claude_code_copy_of_one_version_have_two_identities(tmp_path):
    copilot, claude = make_copy(tmp_path / "agent-orb", "1.4.0"), make_copy(tmp_path / "other" / "1.4.0", "1.4.0")
    assert engine_wire.identity(copilot) != engine_wire.identity(claude)


def test_two_checkouts_have_two_identities(tmp_path):
    one, two = make_copy(tmp_path / "one", "1.4.0", git="folder"), make_copy(tmp_path / "two", "1.4.0", git="folder")
    assert engine_wire.identity(one) != engine_wire.identity(two)


def test_a_windows_checkout_is_the_same_whatever_the_case_of_its_path(tmp_path):
    upper, lower = tmp_path / "Work" / "Orb", tmp_path / "work" / "orb"
    assert engine_wire.identity(upper, "win32") == engine_wire.identity(lower, "win32")


def test_a_posix_checkout_is_told_apart_by_the_case_of_its_path(tmp_path):
    upper, lower = tmp_path / "Work" / "Orb", tmp_path / "work" / "orb"
    assert engine_wire.identity(upper, "linux") != engine_wire.identity(lower, "linux")


def test_an_identity_is_twelve_hex_digits(tmp_path):
    found = engine_wire.identity(make_copy(tmp_path / "1.4.0", "1.4.0"))
    assert len(found) == 12 and int(found, 16) >= 0


def test_every_file_of_a_host_is_named_for_its_identity_in_the_state_folder(tmp_path):
    files = [f(tmp_path, "abc123") for f in (engine_wire.record_file, engine_wire.lock_file, engine_wire.start_lock_file, engine_wire.log_file)]
    assert [f.name for f in files] == ["engine-host.abc123.json", "engine-host.abc123.lock", "engine-host.abc123.start.lock", "engine-host.abc123.log"]
    assert {f.parent for f in files} == {tmp_path / ".config" / "orb" / "state"}


def test_a_record_holds_port_token_pid_and_version_only(tmp_path):
    engine_wire.write_record(tmp_path, "abc123", HostRecord(4000, "secret", 7, "1.4.0"))
    assert json.loads(engine_wire.record_file(tmp_path, "abc123").read_text(encoding="utf-8")) == {
        "port": 4000, "token": "secret", "pid": 7, "version": "1.4.0"}


def test_a_record_is_read_back_as_it_was_written(tmp_path):
    engine_wire.write_record(tmp_path, "abc123", HostRecord(4000, "secret", 7, None))
    assert engine_wire.read_record(tmp_path, "abc123") == HostRecord(4000, "secret", 7, None)


@pytest.mark.parametrize("record", [
    {"transport": "unix", "address": "/x", "token": "t", "pid": 7},
    {"port": "4000", "token": "t", "pid": 7, "version": None},
    {"port": 4000, "token": 5, "pid": 7, "version": None},
    {"port": 4000, "token": "t", "pid": "1", "version": None},
    {"port": 4000, "token": "t", "pid": 7, "version": 3},
    {"port": 4000, "token": "t", "pid": 7},
    {"port": 70000, "token": "t", "pid": 7, "version": None},
    {"port": 0, "token": "t", "pid": 7, "version": None},
    {"port": True, "token": "t", "pid": 7, "version": None},
])
def test_a_record_that_is_not_the_current_shape_is_no_record(tmp_path, record):
    engine_wire.record_file(tmp_path, "abc123").parent.mkdir(parents=True)
    engine_wire.record_file(tmp_path, "abc123").write_text(json.dumps(record), encoding="utf-8")
    assert engine_wire.read_record(tmp_path, "abc123") is None


@pytest.mark.parametrize("pid", [0, -1, 1, True, 2**31], ids=["its process group", "every process", "the first process", "a boolean", "beyond any pid"])
def test_a_record_whose_pid_cannot_name_one_process_is_no_record(tmp_path, pid):
    engine_wire.record_file(tmp_path, "abc123").parent.mkdir(parents=True)
    engine_wire.record_file(tmp_path, "abc123").write_text(
        json.dumps({"port": 4000, "token": "t", "pid": pid, "version": None}), encoding="utf-8")
    assert engine_wire.read_record(tmp_path, "abc123") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; on Windows the profile folder's own ACL keeps other users out")
def test_writing_a_record_closes_its_folder_to_other_users(tmp_path):
    folder = engine_wire.record_file(tmp_path, "abc123").parent
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    engine_wire.write_record(tmp_path, "abc123", HostRecord(4000, "secret", 7, None))
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_one_identitys_record_is_not_another_identitys(tmp_path):
    engine_wire.write_record(tmp_path, "abc123", HostRecord(4000, "secret", 7, None))
    assert engine_wire.read_record(tmp_path, "def456") is None


def test_a_record_is_removed_only_by_the_pid_it_names(tmp_path):
    engine_wire.write_record(tmp_path, "abc123", HostRecord(4000, "secret", 7, None))
    engine_wire.remove_record(tmp_path, "abc123", 8)
    assert engine_wire.read_record(tmp_path, "abc123") is not None
    engine_wire.remove_record(tmp_path, "abc123", 7)
    assert engine_wire.read_record(tmp_path, "abc123") is None


def test_a_lock_held_elsewhere_is_not_taken_when_there_is_no_wait(tmp_path):
    held = engine_wire.hold_lock(tmp_path / "l")
    try:
        assert engine_wire.hold_lock(tmp_path / "l") is None
    finally:
        held.close()


def test_a_lock_held_elsewhere_is_given_up_after_the_wait(tmp_path):
    held = engine_wire.hold_lock(tmp_path / "l")
    try:
        started = time.monotonic()
        assert engine_wire.hold_lock(tmp_path / "l", wait_s=0.3) is None
        assert time.monotonic() - started >= 0.3
    finally:
        held.close()


def test_a_lock_released_during_the_wait_is_taken(tmp_path):
    held = engine_wire.hold_lock(tmp_path / "l")
    threading.Timer(0.2, held.close).start()
    taken = engine_wire.hold_lock(tmp_path / "l", wait_s=10)
    assert taken is not None
    taken.close()


@pytest.fixture
def linked():
    ours, host = socket.socketpair()
    link = Link(ours)
    yield link, host
    link.close()
    host.close()


def test_a_request_is_answered_with_the_line_the_host_sends_back(linked):
    link, host = linked
    host.sendall(b'{"ok": true}\n')
    assert link.request({"op": "ping"}, 5) == {"ok": True}
    assert json.loads(host.recv(1024)) == {"op": "ping"}


def test_a_request_the_host_does_not_answer_in_time_raises_a_timeout_and_closes_the_link(linked):
    link, _ = linked
    started = time.monotonic()
    with pytest.raises(WireTimeout, match="did not answer within 0.3 s"):
        link.request({"op": "ping"}, 0.3)
    assert 0.28 <= time.monotonic() - started < 3.0
    with pytest.raises(WireBroken):
        link.request({"op": "ping"}, 0.3)


def test_a_request_the_host_hangs_up_on_raises_broken(linked):
    link, host = linked
    host.close()
    with pytest.raises(WireBroken):
        link.request({"op": "ping"}, 5)


@pytest.mark.parametrize("sent", [b"not json\n", b"[1]\n"])
def test_a_reply_that_is_not_an_object_raises_broken(linked, sent):
    link, host = linked
    host.sendall(sent)
    with pytest.raises(WireBroken, match="something that is not"):
        link.request({"op": "ping"}, 5)


@pytest.mark.parametrize("answer", [{}, {"ok": False}, {"ok": 1}, {"error": "x"}])
def test_a_listener_that_answers_the_hello_with_anything_but_ok_true_is_not_connected_to(answer):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    done = threading.Event()

    def answer_one_hello():
        conn, _ = listener.accept()
        conn.makefile("rb").readline()
        conn.sendall(json.dumps(answer).encode("utf-8") + b"\n")
        done.wait(5)
        conn.close()

    threading.Thread(target=answer_one_hello, daemon=True).start()
    try:
        with pytest.raises(engine_wire.WireError, match="the engine host refused the connection"):
            engine_wire.connect(HostRecord(listener.getsockname()[1], "secret", 7, None))
    finally:
        done.set()
        listener.close()


def test_a_timeout_is_a_kind_of_wire_error_and_so_is_a_broken_connection():
    assert issubclass(WireTimeout, engine_wire.WireError) and issubclass(WireBroken, engine_wire.WireError)
