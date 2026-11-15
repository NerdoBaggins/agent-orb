import json
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).with_name("engine.py")


def converse(settings, *requests):
    """The replies to a load with `settings` and then each request, from one engine process."""
    lines = [{"op": "load", "settings": settings}, *requests]
    done = subprocess.run([sys.executable, str(ENGINE)], input="".join(json.dumps(r) + "\n" for r in lines),
                          capture_output=True, text=True, timeout=30)
    return [json.loads(line) for line in done.stdout.splitlines()][1:]


def test_the_fake_answers_an_unknown_voice_with_the_exact_text():
    [reply] = converse({"voices": ["ann", "bob"]}, {"op": "render", "text": "x", "voice": "zed"})
    assert reply == {"error": "no fake voice named 'zed'"}


def test_the_fake_has_no_voices_op_without_a_catalogue():
    [reply] = converse({}, {"op": "voices"})
    assert reply == {"error": "unknown op 'voices'"}
