"""What the orb answers itself while the agent is busy: a replay of the agent's last reply, a sentence about what
is running, or the replies that finished while the user was away. Anything else is the agent's to answer."""
import re

REPEAT = re.compile(
    r"\b(repeat (that|it)\b|repeat the last (thing|reply|answer|sentence|part|message)|repeat what (he|she|it|you|they) said"
    r"|say (that|it) again|what did (he|she|it|you|they) (just )?say|come again|read (that|it) (back|again))", re.I)
STATUS = re.compile(
    r"\b(what('s| is| are) (it|he|she|you|they)('re| are)? (doing|working on|running)|what('s| is) (running|happening|going on)"
    r"|what('s| is) the (status|progress)|(any|what) (progress|updates?)\b|how('s| is) it going"
    r"|are you (still )?(working|busy)|still (working|busy)\b)", re.I)
MISSED = re.compile(
    r"\b(what did i miss|what have i missed|what('s| is| was) i missed|catch me up|what happened (while|when) i|"
    r"anything (new|i missed)|read (me )?(what i missed|the new (replies|messages)))", re.I)
LONGEST_SPOKEN_NAME = 50
MOST_NAMED = 3


def side_intent(text: str) -> str | None:
    if MISSED.search(text):
        return "missed"
    if REPEAT.search(text):
        return "repeat"
    if STATUS.search(text):
        return "status"
    return None


def mention(description: str, kind: str) -> str:
    """A task's label as it is spoken: a long command line becomes 'a shell'."""
    return description if len(description) <= LONGEST_SPOKEN_NAME else f"a {kind}"


def status_sentence(agent: str, doing: str | None, tasks: list[dict]) -> str:
    first = f"{agent} is in the middle of {doing}." if doing else f"{agent} is still working on your last request."
    if not tasks:
        return first
    names = [mention(t["description"], t["kind"]) for t in tasks]
    count = "One background task is" if len(tasks) == 1 else f"{len(tasks)} background tasks are"
    more = f", and {len(names) - MOST_NAMED} more" if len(names) > MOST_NAMED else ""
    return f"{first} {count} running: {', '.join(names[:MOST_NAMED])}{more}."
