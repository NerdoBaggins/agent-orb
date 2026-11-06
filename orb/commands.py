"""A built-in agent command the orb types into the attached session's own terminal, such as `/compact <focus>`."""
import re
from dataclasses import dataclass

NAME = re.compile(r"[a-z][a-z0-9-]*")
MAX_ARGUMENT = 4000
LINE_BREAKS = "\u2028\u2029\u0085"
# Claude Code types a built-in that takes no argument with text after it to the model as a prompt, the user's own words.
TAKES_ARGUMENT = frozenset({"color", "compact", "effort", "fast", "model", "rename"})


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: str
    argument: str

    @property
    def line(self) -> str:
        return "".join(self.parts)

    @property
    def parts(self) -> tuple[str, ...]:
        """What is typed, write by write: a line that arrives as one long write is a paste, which goes to the agent."""
        return (f"/{self.name} ", self.argument) if self.argument else (f"/{self.name}",)


def is_name(value) -> bool:
    return isinstance(value, str) and NAME.fullmatch(value) is not None


def control_characters(text: str) -> list[str]:
    return [f"U+{ord(c):04X}" for c in text if ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 or c in LINE_BREAKS]


def parse(name, argument, allowed: list[str]) -> Command:
    name = name[1:] if isinstance(name, str) and name.startswith("/") else name
    if not is_name(name):
        raise CommandError(f"{name!r} is not a command name")
    if name not in allowed:
        raise CommandError(f"/{name} is not on the orb's commands allowlist ({', '.join(allowed) or 'empty'}); "
                           "it is the `commands` list in ~/.config/orb/config.json")
    argument = "" if argument is None else argument
    if not isinstance(argument, str):
        raise CommandError("a command's argument is text")
    argument = argument.strip(" ")
    if argument and name not in TAKES_ARGUMENT:
        raise CommandError(f"/{name} takes no argument")
    found = control_characters(argument)
    if found:
        raise CommandError(f"a command's argument is one line of text; it holds {', '.join(sorted(set(found)))}")
    if len(argument) > MAX_ARGUMENT:
        raise CommandError(f"a command's argument is at most {MAX_ARGUMENT} characters, got {len(argument)}")
    return Command(name, argument)
