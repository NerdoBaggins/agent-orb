import re
from pathlib import Path

HEADING = re.compile(r"^## (.+?)(?: [—-] (\d{4}-\d{2}-\d{2}))?\s*$")
LINK = re.compile(r"!?\[([^\]]+)\]\((?:[^()]|\([^()]*\))*\)")


def parse(text: str) -> list[dict]:
    releases: list[dict] = []
    release = section = None
    for line in text.splitlines():
        if heading := HEADING.match(line):
            release, section = {"version": heading[1], "date": heading[2], "sections": []}, None
            releases.append(release)
        elif release is None:
            continue
        elif line.startswith("### "):
            section = {"heading": line[4:].strip(), "entries": []}
            release["sections"].append(section)
        elif line.startswith("- "):
            if section is None:
                section = {"heading": "", "entries": []}
                release["sections"].append(section)
            section["entries"].append(line[2:].strip())
        elif section is not None and section["entries"] and line.startswith("  ") and line.strip():
            section["entries"][-1] += " " + line.strip()
    for release in releases:
        release["sections"] = [{**s, "entries": [LINK.sub(r"\1", e) for e in s["entries"]]} for s in release["sections"] if s["entries"]]
    return [r for r in releases if r["sections"]]


def load(path: Path) -> list[dict]:
    try:
        return parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
