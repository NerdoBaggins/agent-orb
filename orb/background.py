"""What the attached agent is running in the background, as the bar's task list shows it: kind, description, start.
Pure functions over what each agent records; the links do the reading."""
import json
import re

TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")
BACKGROUND_TOOLS = {"Bash": "shell", "Agent": "agent", "Task": "agent"}


def task(kind: str, description: str, started_at: str | None) -> dict:
    return {"kind": kind, "description": description, "startedAt": started_at}


def _blocks(entry: dict) -> list:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def _finished_ids(entries: list[dict]) -> set[str]:
    """Task ids Claude Code has reported done: its task-notification deliveries, and TaskStop calls."""
    done = set()
    for entry in entries:
        attachment = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
        if attachment.get("type") == "queued_command" and attachment.get("commandMode") == "task-notification":
            done.update(TASK_ID.findall(str(attachment.get("prompt", ""))))
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if entry.get("type") == "user" and str(message.get("content", "")).startswith("<task-notification>"):
            done.update(TASK_ID.findall(str(message["content"])))
        for block in _blocks(entry):
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").startswith("<task-notification>"):
                done.update(TASK_ID.findall(block["text"]))
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "TaskStop":
                done.add(str((block.get("input") or {}).get("task_id", "")))
    return done


def claude_running_tasks(entries: list[dict] | None) -> list[dict]:
    """Background shells and agents a Claude Code transcript started and has not yet reported finished: those asked
    to run in the background, and agents Claude Code ran in the background by itself, which it records as async in
    the tool's result rather than in its input. The user stopping the background agents is recorded with no ids, and
    ends every agent running then."""
    started, asked = {}, set()
    for entry in entries or ():
        for block in _blocks(entry):
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in BACKGROUND_TOOLS:
                started[block.get("id")] = task(BACKGROUND_TOOLS[block["name"]],
                                                str((block.get("input") or {}).get("description") or block["name"]),
                                                entry.get("timestamp"))
                if (block.get("input") or {}).get("run_in_background"):
                    asked.add(block.get("id"))
    running = {}
    for entry in entries or ():
        if entry.get("type") == "system" and entry.get("subtype") == "agents_killed":
            running = {task_id: t for task_id, t in running.items() if t["kind"] != "agent"}
            continue
        result = entry.get("toolUseResult")
        if not isinstance(result, dict):
            continue
        task_id = result.get("backgroundTaskId") or result.get("agentId")
        use_id = next((b.get("tool_use_id") for b in _blocks(entry) if isinstance(b, dict) and b.get("tool_use_id")), None)
        if task_id and use_id in started and (use_id in asked or result.get("isAsync") is True):
            running[str(task_id)] = started[use_id]
    done = _finished_ids(entries or [])
    return [t for task_id, t in running.items() if task_id not in done]


def copilot_running_tasks(listed: list[dict]) -> list[dict]:
    """Copilot's own task list (`session.rpc.tasks.list`), running ones only: idle sub-agents and finished shells
    stay in that list for the life of the session."""
    return [task(str(t.get("type", "task")), str(t.get("description") or t.get("command") or t.get("type")),
                 t.get("startedAt")) for t in listed if isinstance(t, dict) and t.get("status") == "running"]


def same(a: list[dict], b: list[dict]) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
