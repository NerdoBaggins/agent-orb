import pytest

from orb.side import side_intent, status_sentence


@pytest.mark.parametrize("text", [
    "I'm sorry, can you repeat the last thing he said?", "Repeat that.", "say that again please",
    "what did he just say", "Could you read it back"])
def test_asking_for_the_last_reply_again_is_a_repeat(text):
    assert side_intent(text) == "repeat"


@pytest.mark.parametrize("text", [
    "what are you working on", "What's running right now?", "any progress", "how's it going",
    "are you still working?", "What's the status"])
def test_asking_what_is_going_on_is_a_status_question(text):
    assert side_intent(text) == "status"


@pytest.mark.parametrize("text", [
    "repeat the build with verbose flags", "update the readme", "run the tests again", "what time is it",
    "show me a panel of the files", "merge it"])
def test_a_real_request_is_left_to_the_agent(text):
    assert side_intent(text) is None


def test_status_names_what_the_agent_is_in_the_middle_of_and_the_background_tasks():
    tasks = [{"kind": "shell", "description": "Poll checks"}, {"kind": "agent", "description": "Phase 1 review"}]
    assert status_sentence("Copilot", "Run the tests", tasks) == (
        "Copilot is in the middle of Run the tests. 2 background tasks are running: Poll checks, Phase 1 review.")


def test_status_without_a_known_step_or_tasks_still_says_it_is_working():
    assert status_sentence("Copilot", None, []) == "Copilot is still working on your last request."


def test_a_command_line_is_spoken_as_its_kind_and_a_long_list_is_cut():
    tasks = [{"kind": "shell", "description": "cd /home/me/projects/example-app && git status --short | head"}] + [
        {"kind": "agent", "description": f"Task {i}"} for i in range(4)]
    spoken = status_sentence("Copilot", None, tasks)
    assert "a shell, Task 0, Task 1, and 2 more" in spoken and "/home" not in spoken
