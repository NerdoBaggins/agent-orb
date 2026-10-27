from orb.background import claude_running_tasks, copilot_running_tasks


def use(use_id, name, description, background=True, at="2026-10-09T21:00:00Z"):
    return {"type": "assistant", "timestamp": at, "message": {"content": [
        {"type": "tool_use", "id": use_id, "name": name,
         "input": {"command": "sleep 300", "description": description, "run_in_background": background}}]}}


def started(use_id, task_id, key="backgroundTaskId"):
    return {"type": "user", "toolUseResult": {key: task_id},
            "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": "started"}]}}


def notified(task_id):
    return {"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "task-notification",
        "prompt": f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>completed</status>"}}


def test_a_background_shell_runs_until_its_notification_arrives():
    entries = [use("u1", "Bash", "Poll checks until done"), started("u1", "b1")]
    assert claude_running_tasks(entries) == [
        {"kind": "shell", "description": "Poll checks until done", "startedAt": "2026-10-09T21:00:00Z"}]
    assert claude_running_tasks(entries + [notified("b1")]) == []


def test_a_background_agent_is_listed_as_an_agent():
    entries = [use("u2", "Agent", "Phase 1 review"), started("u2", "a1", key="agentId")]
    assert [t["kind"] for t in claude_running_tasks(entries)] == ["agent"]


def test_an_agent_claude_code_ran_in_the_background_by_itself_is_listed_until_it_reports():
    launch = {"type": "assistant", "timestamp": "2026-10-26T19:00:00Z", "message": {"content": [
        {"type": "tool_use", "id": "u4", "name": "Agent", "input": {"description": "PR 77 fix check", "prompt": "..."}}]}}
    async_result = {"type": "user", "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a4"},
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "u4", "content": "launched"}]}}
    assert claude_running_tasks([launch, async_result]) == [
        {"kind": "agent", "description": "PR 77 fix check", "startedAt": "2026-10-26T19:00:00Z"}]
    assert claude_running_tasks([launch, async_result, notified("a4")]) == []


def test_an_agent_that_ran_in_the_foreground_is_not_running():
    launch = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "u5", "name": "Agent", "input": {"description": "Quick lookup", "prompt": "..."}}]}}
    assert claude_running_tasks([launch, started("u5", "a5", key="agentId")]) == []


def test_a_stopped_task_and_a_foreground_command_are_not_running():
    stop = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "u9", "name": "TaskStop",
                                                          "input": {"task_id": "b1"}}]}}
    entries = [use("u1", "Bash", "Long build"), started("u1", "b1"), stop,
               use("u3", "Bash", "Quick check", background=False), started("u3", "b3")]
    assert claude_running_tasks(entries) == []


def test_a_notification_typed_as_a_user_turn_also_ends_the_task():
    entries = [use("u1", "Bash", "Tests"), started("u1", "b1"),
               {"type": "user", "message": {"content": "<task-notification>\n<task-id>b1</task-id>"}}]
    assert claude_running_tasks(entries) == []


def test_copilot_lists_only_running_tasks():
    listed = [{"type": "agent", "description": "Phase 1 review", "status": "idle", "startedAt": "t0"},
              {"type": "shell", "description": "Poll checks until done", "status": "running", "startedAt": "t1"},
              {"type": "shell", "description": "Old build", "status": "completed", "startedAt": "t2"}]
    assert copilot_running_tasks(listed) == [{"kind": "shell", "description": "Poll checks until done", "startedAt": "t1"}]


def test_stopping_the_background_agents_ends_every_agent_running_but_not_shells():
    entries = [use("u2", "Agent", "Phase 1 review"), started("u2", "a1", key="agentId"),
               use("u1", "Bash", "Poll checks"), started("u1", "b1"),
               {"type": "system", "subtype": "agents_killed"},
               use("u3", "Agent", "Started after"), started("u3", "a3", key="agentId")]
    assert [t["description"] for t in claude_running_tasks(entries)] == ["Poll checks", "Started after"]
