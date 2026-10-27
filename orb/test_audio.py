import pytest

from orb.audio import CODE_BLOCK, speakable, split_sentences


def test_split_sentences_splits_on_terminators_and_newlines():
    assert split_sentences("First one here. Second one there!\nThird one now?") == ["First one here.", "Second one there!", "Third one now?"]


def test_split_sentences_merges_short_fragments():
    assert split_sentences("Yes. That is the whole answer.") == ["Yes. That is the whole answer."]
    assert split_sentences("The build passed. Done.") == ["The build passed. Done."]


def test_split_sentences_of_empty_text_is_empty():
    assert split_sentences("  \n ") == []


@pytest.mark.parametrize("text, spoken", [
    ("## Status\n- **Automated comments:** from `11buildacc`, see [the docs](http://x).\n1. Both are _open_.",
     "Status\nAutomated comments: from 11buildacc, see the docs.\nBoth are open."),
    ("See [the docs](https://x.org/a_(b)) now.", "See the docs now."),
    ('See [docs](https://x.org "Title") now.', "See docs now."),
    ("The answer is **5 * 3 = 15**.", "The answer is 5 * 3 = 15."),
    ("**bold with *italic* inside**", "bold with italic inside"),
    ("Pass **kwargs on, see **Note** below.", "Pass **kwargs on, see Note below."),
    ("Pass f(**kwargs) and (**Note**) here.", "Pass f(**kwargs) and (Note) here."),
    ("Run `rm *.pyc` and `ls | grep x`.", "Run rm *.pyc and ls | grep x."),
    ("First line\nsecond line", "First line\nsecond line"),
    ("<details>\nThe hidden answer is forty two.\n</details>", "<details>\nThe hidden answer is forty two.\n</details>"),
    ("Hello <!-- a *b* note --> World", "Hello <!-- a b note --> World"),
    ("Replace <name> with your name, under <ORB_ROOT>/orb.", "Replace <name> with your name, under <ORB_ROOT>/orb."),
    ("Call foo<T>() on a List<String>.", "Call foo<T>() on a List<String>."),
    ("See ![the architecture diagram](x.png) above.", "See the architecture diagram above."),
    ("See ![a *bold* alt](x.png) here.", "See a bold alt here."),
    ("See ![my\\_config &lt;b&gt; caf&eacute;](x.png) here.", "See my_config <b> café here."),
    ("Use Box<B> and Promise<A>.", "Use Box<B> and Promise<A>."),
    ("~~gone~~ kept", "gone kept"),
    ("&#32;lead", "lead"),
    ("Some text\n\n    an indented code block", f"Some text\n{CODE_BLOCK}"),
])
def test_speakable_drops_the_marks_and_keeps_the_words(text, spoken):
    assert speakable(text) == spoken


@pytest.mark.parametrize("text", ["my_var_name is 2 * 3 * 4.", "Delete *.py and *.md files.", "Pass **kwargs on.",
                                  "Pipe it: ls | grep x.", "def f(*args, **kwargs): pass", "Pass **kwargs and **args on."])
def test_speakable_leaves_text_that_only_looks_like_markdown_alone(text):
    assert speakable(text) == text


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -"])
def test_speakable_drops_a_rule_line(rule):
    assert speakable(f"Above\n\n{rule}\n\nRun ls | grep x.") == "Above\nRun ls | grep x."


@pytest.mark.parametrize("table", ["| PR | State |\n|---|---|\n| 2147 | open |", "PR | State\n---|---\n2147 | open",
                                   "| PR | State |\n| --- | - |\n| 2147 | open |", "- | PR | State |\n  |---|:-:|\n  | 2147 | open |"])
def test_speakable_reads_a_tables_rows_as_cells(table):
    assert speakable(table) == "PR, State\n2147, open"


def test_a_single_column_table_is_read():
    assert speakable("| only |\n|---|\n| one |") == "only\none"


def test_a_pipe_after_a_table_and_a_blank_line_is_left_as_written():
    assert speakable("| a | b |\n|---|---|\n| 1 | 2 |\n\n- use x | y to pipe") == "a, b\n1, 2\nuse x | y to pipe"


def test_speakable_names_a_code_block_instead_of_reading_it():
    assert speakable("Here:\n```swift\nlet x = 1\n```\nDone.") == f"Here:\n{CODE_BLOCK}\nDone."


def test_an_empty_cell_or_padding_in_a_cell_is_not_read():
    assert speakable("| a | |\n|---|---|\n| `x ` | y |") == "a\nx, y"
