import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orb import agents, commands
from orb.copilot import CopilotLink, SessionMoved, events_path, read_door
from orb.state import copilot_channel_file

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HERE = Path(__file__).parent
FAKE_SESSION = """
import { openDoor } from %(door)s;
import { copilotRoutes } from %(routes)s;
const say = (line) => process.stdout.write(JSON.stringify(line) + "\\n");
const session = {
  sessionId: process.env.SESSION,
  send: async ({ prompt }) => { say({ sent: prompt }); return "m1"; },
  abort: async () => say({ aborted: true }),
  rpc: {
    tasks: { list: async () => ({ tasks: [{ id: "shell-7" }] }) },
    commands: { enqueue: async ({ command }) => {
      say({ queued: command });
      return process.env.QUEUES === "no" ? { queued: false } : { queued: true, queueId: "q1" };
    } },
  },
};
openDoor({ recordFile: process.env.RECORD, record: { sessionId: process.env.SESSION, repoRoot: "/r" },
           routes: copilotRoutes(session), log: () => {} });
""" % {"door": json.dumps((HERE.parent / "channel" / "door.mjs").as_uri()),
       "routes": json.dumps((HERE / "routes.mjs").as_uri())}


class Door:
    def __init__(self, record: Path, session: str = "s1", queues: bool = True):
        env = {**os.environ, "RECORD": str(record), "SESSION": session, "QUEUES": "yes" if queues else "no"}
        self.proc = subprocess.Popen(["node", "--input-type=module", "-e", FAKE_SESSION], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        deadline = time.time() + 10
        while time.time() < deadline:
            if record.exists() and json.loads(record.read_text(encoding="utf-8"))["pid"] == self.proc.pid:
                return
            time.sleep(0.05)
        self.close()
        raise AssertionError(f"no door record; stderr: {self.proc.stderr.read()}")

    def said(self) -> dict:
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def door(tmp_path):
    opened = Door(tmp_path / "copilot-channel.json")
    yield opened
    opened.close()


def linked(tmp_path, session="s1") -> CopilotLink:
    link = CopilotLink(tmp_path / "copilot-channel.json", home=tmp_path)
    link.session_id = session
    return link


def test_the_orb_finds_the_session_through_the_recorded_door(door, tmp_path):
    link = CopilotLink(tmp_path / "copilot-channel.json", home=tmp_path)
    assert link.probe() and link.session_id == "s1"


def test_a_send_reaches_the_session_with_the_prompt_and_returns_its_message_id(door, tmp_path):
    assert linked(tmp_path)._call_door("send", 5, "What time is it?") == {"messageId": "m1"}
    assert door.said() == {"sent": "What time is it?"}


def test_a_send_meant_for_a_session_the_door_no_longer_serves_never_reaches_it(door, tmp_path):
    with pytest.raises(SessionMoved):
        linked(tmp_path, session="before-clear")._call_door("send", 5, "hello")
    linked(tmp_path)._call_door("send", 5, "the next one")
    assert door.said() == {"sent": "the next one"}


def test_a_blank_prompt_is_refused_before_it_reaches_the_session(door, tmp_path):
    with pytest.raises(agents.LinkError, match="400"):
        linked(tmp_path)._call_door("send", 5, "   ")
    linked(tmp_path)._call_door("send", 5, "the next one")
    assert door.said() == {"sent": "the next one"}


def test_abort_reaches_the_session(door, tmp_path):
    assert linked(tmp_path)._call_door("abort", 5) == {"aborted": True} and door.said() == {"aborted": True}


def test_an_abort_meant_for_a_session_the_door_no_longer_serves_is_refused(door, tmp_path):
    with pytest.raises(SessionMoved):
        linked(tmp_path, session="before-clear")._call_door("abort", 5)


def test_the_background_tasks_come_from_the_session(door, tmp_path):
    assert linked(tmp_path)._call_door("tasks", 5) == {"tasks": [{"id": "shell-7"}]}


def test_a_command_is_queued_in_the_session_as_typed(door, tmp_path):
    assert linked(tmp_path)._call_door("command", 5, "/compact keep the plan") == {"queueId": "q1"}
    assert door.said() == {"queued": "/compact keep the plan"}


def test_a_command_meant_for_a_session_the_door_no_longer_serves_is_not_queued(door, tmp_path):
    with pytest.raises(SessionMoved):
        linked(tmp_path, session="before-clear")._call_door("command", 5, "/compact")
    linked(tmp_path)._call_door("command", 5, "/context")
    assert door.said() == {"queued": "/context"}


def test_a_command_queued_after_the_session_moved_follows_it_to_the_new_one(door, tmp_path):
    link = linked(tmp_path, session="before-clear")
    link.type_command(commands.Command("compact", "keep the plan"))
    assert link.session_id == "s1" and door.said() == {"queued": "/compact keep the plan"}


def test_a_command_copilot_does_not_log_is_refused_before_it_is_queued(door, tmp_path):
    with pytest.raises(agents.LinkError, match="keeps no trace of /context"):
        linked(tmp_path).type_command(commands.Command("context", ""))
    linked(tmp_path).type_command(commands.Command("compact", ""))
    assert door.said() == {"queued": "/compact"}


def write_log(tmp_path, *events):
    log = events_path(tmp_path, "s1")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return log


def test_the_turn_is_open_while_copilot_works_on_a_message_and_closed_once_it_answers(tmp_path):
    link = linked(tmp_path)
    asked = {"type": "user.message", "data": {"content": "go"}}
    write_log(tmp_path, asked)
    assert link.turn_open()
    write_log(tmp_path, asked, {"type": "assistant.message", "data": {"content": "Done."}}, {"type": "assistant.turn_end"})
    assert not link.turn_open()


def test_text_that_is_not_a_slash_command_is_refused_before_it_is_queued(door, tmp_path):
    with pytest.raises(agents.LinkError, match="400"):
        linked(tmp_path)._call_door("command", 5, "compact")
    linked(tmp_path)._call_door("command", 5, "/context")
    assert door.said() == {"queued": "/context"}


def test_a_session_that_cannot_queue_commands_is_reported_rather_than_taken_as_queued(tmp_path):
    record = tmp_path / "copilot-channel.json"
    opened = Door(record, queues=False)
    try:
        with pytest.raises(agents.LinkError, match="501"):
            linked(tmp_path)._call_door("command", 5, "/compact")
    finally:
        opened.close()


def test_only_a_compaction_after_the_command_was_queued_shows_it_ran(door, tmp_path):
    link = linked(tmp_path)
    compacted = {"type": "session.compaction_complete", "data": {"success": True, "trigger": "manual"}}
    log = write_log(tmp_path, compacted)
    done = json.dumps(compacted) + "\n"
    link.type_command(commands.Command("compact", "keep the plan"))
    assert not link.ran_since("compact", datetime.now(timezone.utc))
    with log.open("a", encoding="utf-8") as f:
        f.write(done)
    assert link.ran_since("compact", datetime.now(timezone.utc))


def test_the_link_follows_the_door_to_a_new_secret_after_copilot_restarts_the_extension(tmp_path):
    record = tmp_path / "copilot-channel.json"
    link = CopilotLink(record, home=tmp_path)
    first = Door(record, session="before-clear")
    try:
        assert link.probe() and link.session_id == "before-clear"
    finally:
        first.close()
    second = Door(record, session="after-clear")
    try:
        assert link.probe() and link.session_id == "after-clear"
    finally:
        second.close()


def test_a_door_whose_extension_has_exited_is_not_live(tmp_path):
    record = tmp_path / "copilot-channel.json"
    opened = Door(record)
    opened.close()
    assert read_door(record) is None


def test_a_recorded_port_another_program_now_holds_is_given_up_when_its_door_is_gone(tmp_path):
    record = tmp_path / "copilot-channel.json"
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        held = squatter.getsockname()[1]
        record.write_text(json.dumps({"port": held, "secret": "old", "pid": 2 ** 22 + 1, "sessionId": "gone"}),
                          encoding="utf-8")
        opened = Door(record)
        try:
            moved = read_door(record)
            assert moved is not None and moved.port != held
        finally:
            opened.close()


def test_a_recorded_port_an_open_web_server_now_holds_is_given_up_whatever_the_recorded_pid(tmp_path):
    import http.server
    import threading

    class AnswersEverything(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    record = tmp_path / "copilot-channel.json"
    squatter = http.server.HTTPServer(("127.0.0.1", 0), AnswersEverything)
    threading.Thread(target=squatter.serve_forever, daemon=True).start()
    held = squatter.server_address[1]
    record.write_text(json.dumps({"port": held, "secret": "old", "pid": os.getpid(), "sessionId": "gone"}),
                      encoding="utf-8")
    opened = Door(record)
    try:
        moved = read_door(record)
        assert moved is not None and moved.port != held
    finally:
        opened.close()
        squatter.shutdown()


def test_a_recorded_port_another_repos_door_now_holds_is_given_up(tmp_path):
    other = tmp_path / "other" / "copilot-channel.json"
    other.parent.mkdir()
    holder = Door(other, session="other-repo")
    try:
        held = json.loads(other.read_text(encoding="utf-8"))["port"]
        record = tmp_path / "copilot-channel.json"
        record.write_text(json.dumps({"port": held, "secret": "old", "pid": os.getpid(), "sessionId": "gone"}),
                          encoding="utf-8")
        opened = Door(record)
        try:
            moved = read_door(record)
            assert moved is not None and moved.port != held
        finally:
            opened.close()
    finally:
        holder.close()


def test_the_extension_and_the_orb_name_the_same_record(tmp_path):
    script = f"import {{ RECORD_NAME }} from {json.dumps((HERE / 'routes.mjs').as_uri())};" \
             "process.stdout.write(RECORD_NAME);"
    named = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout
    assert named == copilot_channel_file(tmp_path).name
