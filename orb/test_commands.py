import pytest

from orb.commands import MAX_ARGUMENT, CommandError, parse

ALLOWED = ["compact", "context"]


def test_an_allowed_command_with_an_argument_is_typed_as_one_line():
    assert parse("compact", "keep the open questions", ALLOWED).line == "/compact keep the open questions"


def test_a_command_is_typed_before_its_argument_as_a_write_of_its_own():
    assert parse("compact", "keep the plan", ALLOWED).parts == ("/compact ", "keep the plan")


def test_a_command_without_an_argument_is_typed_alone():
    assert parse("/context", "", ALLOWED).line == "/context"


def test_a_command_off_the_allowlist_is_refused():
    with pytest.raises(CommandError, match="not on the orb's commands allowlist"):
        parse("clear", "", ALLOWED)


@pytest.mark.parametrize("name", ["Compact", "comp act", "../compact", "", 7])
def test_a_name_that_is_not_a_command_name_is_refused(name):
    with pytest.raises(CommandError, match="not a command name"):
        parse(name, "", ALLOWED + ["Compact"])


@pytest.mark.parametrize("argument", ["keep x\r/clear", "keep x\n/clear", "keep\x1b[A", "a\tb", "a\x7fb", "a\x85b",
                                      "a\u2028b"])
def test_an_argument_with_a_control_or_line_break_character_is_refused(argument):
    with pytest.raises(CommandError, match="one line of text"):
        parse("compact", argument, ALLOWED)


def test_an_argument_over_the_limit_is_refused():
    with pytest.raises(CommandError, match="at most"):
        parse("compact", "x" * (MAX_ARGUMENT + 1), ALLOWED)


def test_a_command_that_takes_no_argument_is_refused_one():
    with pytest.raises(CommandError, match="takes no argument"):
        parse("context", "and summarise the repo", ALLOWED)


def test_an_argument_that_is_not_text_is_refused():
    with pytest.raises(CommandError, match="is text"):
        parse("compact", ["keep"], ALLOWED)
