"""Drives a real orb the way a person uses it and reports, per flow, whether it worked.

The orb server, its page, its engine protocol and the plugin's own hooks are the real ones. Two things are stubbed:
speech (the built-in `fake` engines) and the Claude Code session (a channel that answers every prompt by writing a
transcript and running the plugin's Stop hooks, as a session would).

    uv run --project <checkout> python verify.py --subject <checkout> [--changed-since REF] [--only FLOW ...]
    uv run --project <checkout> python verify.py --subject <checkout> --benchmark
"""
import argparse
import asyncio
import base64
import fnmatch
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

from aiohttp import ClientSession, web
from playwright.async_api import async_playwright

HEARD = "verify the orb"
FLOW_TIMEOUT_S = 20
HOLD_S = 1.2
MIC_OPENS_S = 10
SLOW_MIC_S = 2
TAP_SETTLES_S = 4
HERE = Path(__file__).resolve().parent
REPLY_DELAY_S = 0.3
PANEL = """<!doctype html><body><button id="go" style="width:200px;height:80px"
  onclick="orb.send('from the verify panel', {speak: false}).catch(() => {})">send</button></body>"""


class Failed(Exception):
    pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(check, what: str, timeout: float = FLOW_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise Failed(f"timed out after {timeout:.0f}s waiting for {what}")


@dataclass
class FakeSession:
    """Stands in for a Claude Code session with the plugin loaded: answers the orb's channel, keeps a transcript in
    Claude Code's shape, and runs the plugin's hooks from its hooks.json at the moments a session would."""
    subject: Path
    home: Path
    repo: Path
    prompts: list = field(default_factory=list)
    hook_failures: list = field(default_factory=list)

    async def start(self, orb) -> None:
        self.orb = orb
        self.secret = secrets.token_hex(16)
        self.session_id = "verify-" + secrets.token_hex(4)
        self.transcript = self.home / "transcript.jsonl"
        self.transcript.touch()
        self.hooks = json.loads((self.subject / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/prompt", self.prompt)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        state = orb.state.state_dir(self.home, self.repo)
        orb.state.write_json(orb.state.channel_file(state), {"port": port, "secret": self.secret, "pid": os.getpid(),
                                                             "repoRoot": str(self.repo), "sessionPid": os.getpid()})
        await self.run_hooks("SessionStart", {"source": "startup"})

    async def stop(self) -> None:
        await self.runner.cleanup()

    def authorized(self, request: web.Request) -> bool:
        return request.headers.get("X-Orb-Secret") == self.secret

    async def health(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPForbidden()
        return web.json_response({"ok": True})

    async def prompt(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPForbidden()
        body = await request.json()
        self.prompts.append((body["text"], body["req"]))
        asyncio.create_task(self.answer(body["text"], body["req"]))
        return web.json_response({"ok": True})

    async def answer(self, text: str, request_id: str) -> None:
        message = f'<channel source="{self.orb.gate.CHANNEL_SERVER}" req="{request_id}">\n{text}\n</channel>'
        await self.run_hooks("UserPromptSubmit", {"prompt": message})
        self.append({"type": "user", "origin": {"kind": "channel", "server": self.orb.gate.CHANNEL_SERVER},
                     "message": {"role": "user", "content": message}})
        await asyncio.sleep(REPLY_DELAY_S)
        reply = reply_to(text)
        self.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
        await self.run_hooks("Stop", {"last_assistant_message": reply, "stop_hook_active": False})

    def append(self, entry: dict) -> None:
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    async def run_hooks(self, event: str, extra: dict) -> None:
        payload = {"session_id": self.session_id, "transcript_path": str(self.transcript), "cwd": str(self.repo),
                   "hook_event_name": event} | extra
        env = os.environ | {"HOME": str(self.home), "CLAUDE_PROJECT_DIR": str(self.repo),
                            "CLAUDE_PLUGIN_ROOT": str(self.subject)}
        for group in self.hooks.get(event, []):
            for hook in group["hooks"]:
                process = await asyncio.create_subprocess_shell(
                    hook["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                _, err = await process.communicate(json.dumps(payload).encode("utf-8"))
                if process.returncode:
                    self.hook_failures.append(f"{event}: {hook['command']} exited {process.returncode}: {err.decode()[-500:]}")


def reply_to(text: str) -> str:
    return f"Verified: {text.strip()}"


@dataclass
class Orb:
    """The subject's own modules, its running server, and the page a person would be looking at."""
    subject: Path
    home: Path
    repo: Path
    token: str = field(default_factory=lambda: secrets.token_hex(16))
    port: int = field(default_factory=free_port)
    speech_frames: int = 0

    def __post_init__(self):
        sys.path.insert(0, str(self.subject))
        from orb import config, gate, headless, logs, state
        self.config, self.gate, self.headless, self.logs, self.state = config, gate, headless, logs, state

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def configure(self) -> None:
        path = self.config.user_config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stt": {"engine": "fake", "fake": {"text": HEARD}},
                                    "tts": {"engine": "fake", "fake": {}}, "speak": True}), encoding="utf-8")

    async def start_server(self) -> None:
        self.server = subprocess.Popen(
            ["uv", "run", "--project", str(self.subject), "python", "-m", "orb.server", "--repo", str(self.repo),
             "--agent", "claude", "--port", str(self.port), "--home", str(self.home)],
            cwd=self.subject, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.server.stdin.write((self.token + "\n").encode())
        self.server.stdin.close()
        await until(self.up, "the orb server to answer /health", timeout=120)

    async def up(self) -> bool:
        if self.server.poll() is not None:
            raise Failed(f"the orb server exited with {self.server.returncode}; see orb.log")
        try:
            return bool(await self.health())
        except OSError:
            return False

    async def health(self) -> dict:
        async with ClientSession() as s:
            async with s.get(f"{self.base}/health", headers={"X-Orb-Token": self.token}) as r:
                return await r.json()

    async def post(self, path: str, body: dict) -> dict:
        async with ClientSession() as s:
            async with s.post(f"{self.base}{path}", params={"token": self.token}, json=body) as r:
                if r.status >= 400:
                    raise Failed(f"POST {path} answered {r.status}: {await r.text()}")
                return await r.json()

    def stop_server(self) -> None:
        self.server.terminate()
        try:
            self.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.server.kill()

    def log_file(self) -> Path:
        return self.logs.log_path(self.state.state_dir(self.home, self.repo), "claude")

    def watch(self, page, console: Path) -> None:
        def socket_opened(ws):
            ws.on("framereceived", lambda payload: self.count_speech(payload))
        page.on("websocket", socket_opened)
        page.on("console", lambda message: append_line(console, f"{message.type}: {message.text}"))
        page.on("pageerror", lambda error: append_line(console, f"pageerror: {error}"))

    def count_speech(self, payload) -> None:
        if isinstance(payload, bytes):
            self.speech_frames += 1


def append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


Window = Literal["shared", "fresh microphone"]
Runs = Literal["always", "on demand"]


@dataclass(frozen=True)
class Flow:
    name: str
    description: str
    fn: Callable[..., Awaitable[None]]
    page: Window
    runs: Runs
    shortcut: str | None  # where it acts through a page script rather than as a person would, and why


FLOWS: dict[str, Flow] = {}


def register(page: Window, runs: Runs, description: str, shortcut: str | None):
    def add(fn):
        FLOWS[fn.__name__] = Flow(fn.__name__, description, fn, page, runs, shortcut)
        return fn
    return add


def flow(description: str, shortcut: str | None = None):
    return register("shared", "always", description, shortcut)


def cold_flow(description: str):
    return register("fresh microphone", "always", description, None)


def extra_flow(description: str):
    return register("fresh microphone", "on demand", description, None)


async def round_trip(orb: Orb, session: FakeSession, act, expect: str) -> int:
    """`act` makes a request; it must reach the session holding `expect`, and its reply must come back through the
    Stop hook into the orb's transcript. Returns how many speech frames reached the page meanwhile."""
    asked, frames = len(session.prompts), orb.speech_frames
    await act()

    async def arrived():
        return next((p for p in session.prompts[asked:] if expect in p[0]), None)

    text, request_id = await until(arrived, f"a prompt holding {expect!r} to reach the session")

    async def replied():
        entries = (await orb.health())["transcript"]
        return next((e for e in entries if e.get("req") == request_id and e.get("reply") == reply_to(text)), None)

    await until(replied, f"the reply to request {request_id} in the orb's transcript")
    await until(lambda: idle(orb), "the orb to return to idle")
    if session.hook_failures:
        raise Failed("a plugin hook failed: " + session.hook_failures.pop())
    return orb.speech_frames - frames


def spoken(frames: int) -> None:
    if not frames:
        raise Failed("the reply came back but no speech reached the page")


def silent(frames: int) -> None:
    if frames:
        raise Failed(f"{frames} speech frames reached the page for a reply that should be silent")


async def idle(orb: Orb) -> bool:
    return (await orb.health())["state"] == "idle"


async def listening(orb) -> bool:
    return (await orb.health())["state"] == "listening"


async def focus_orb(page) -> None:
    """Clicks an empty corner of the orb's side, as a person takes the keyboard back from a panel: a panel forwards
    only Escape."""
    await page.locator("#orb-pane").click(position={"x": 8, "y": 8})


async def mic_ready(page) -> None:
    await page.wait_for_selector("#mic:not([disabled])", timeout=FLOW_TIMEOUT_S * 1000)


async def mic_centre(page) -> None:
    box = await page.locator("#mic").bounding_box()
    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def hold(orb, press, release) -> None:
    """Holds as a person does: until the orb shows it is listening, then for HOLD_S. A press released while the
    microphone is still opening records nothing and says nothing, so a fixed-length hold fails on a cold mic."""
    await press()
    try:
        await until(lambda: listening(orb), "the orb to start listening while the press is held", timeout=MIC_OPENS_S)
        await asyncio.sleep(HOLD_S)
    finally:
        await release()


SLOW_MIC = """(() => {
  const devices = navigator.mediaDevices;
  const open = devices.getUserMedia.bind(devices);
  devices.getUserMedia = (constraints) => new Promise((done) => setTimeout(done, %d)).then(() => open(constraints));
})()""" % (SLOW_MIC_S * 1000)


@flow("The bar says the orb is attached to the session")
async def attached(orb, session, page):
    await until(lambda: health_says(orb, "attached"), "the server to report the session attached")
    await until(lambda: text_starts(page, "#attach", "Attached"), "the bar to say Attached")


async def health_says(orb, key):
    return (await orb.health())[key]


async def text_starts(page, selector, prefix):
    return (await page.locator(selector).inner_text()).startswith(prefix)


@cold_flow("A tap on the mic button before the microphone has ever opened leaves the orb idle, not recording")
async def tap_mic(orb, session, page):
    await mic_ready(page)
    asked = len(session.prompts)
    await mic_centre(page)
    await page.mouse.down()
    await page.mouse.up()
    await asyncio.sleep(TAP_SETTLES_S)
    state = (await orb.health())["state"]
    if state != "idle":
        raise Failed(f"{TAP_SETTLES_S:.0f}s after a tap the orb is {state!r}, not idle")
    if len(session.prompts) != asked:
        raise Failed("a tap sent a request to the session")


@flow("Holding the mic button with the mouse records, sends what was heard, and speaks the reply")
async def hold_mic(orb, session, page):
    await mic_ready(page)

    async def act():
        await mic_centre(page)
        await hold(orb, page.mouse.down, page.mouse.up)

    spoken(await round_trip(orb, session, act, HEARD))


@flow("A mic press that arrives only as mousedown, with no pointer events, still records",
      shortcut="dispatches bare mouse events, since a real mouse always sends pointer events first")
async def hold_mic_mousedown_only(orb, session, page):
    await mic_ready(page)

    async def act():
        await hold(orb, lambda: page.dispatch_event("#mic", "mousedown"), lambda: page.dispatch_event("#mic", "mouseup"))

    spoken(await round_trip(orb, session, act, HEARD))


@flow("Holding the push-to-talk key records, sends what was heard, and speaks the reply")
async def hold_key(orb, session, page):
    await mic_ready(page)
    await focus_orb(page)
    key = (await orb.health())["ptt_key"]

    async def act():
        await hold(orb, lambda: page.keyboard.down(key), lambda: page.keyboard.up(key))

    spoken(await round_trip(orb, session, act, HEARD))


@extra_flow(f"With a microphone that takes {SLOW_MIC_S:.0f}s to open, holding the mic button still records and sends")
async def hold_mic_slow_open(orb, session, page):
    await page.evaluate(SLOW_MIC)
    await mic_ready(page)

    async def act():
        await mic_centre(page)
        await hold(orb, page.mouse.down, page.mouse.up)

    spoken(await round_trip(orb, session, act, HEARD))


@flow("Enter opens the type box, and Enter in it sends the text and speaks the reply")
async def typed_send(orb, session, page):
    spoken(await round_trip(orb, session, lambda: type_request(page, "typed by verify"), "typed by verify"))


async def type_request(page, text):
    await focus_orb(page)
    if await page.locator("#typed").is_visible():
        await page.locator("#typed").click()
    else:
        await page.keyboard.press("Enter")
    await page.keyboard.type(text)
    await page.keyboard.press("Enter")
    await focus_orb(page)


@flow("A panel posted to the server opens as a tab and renders its page")
async def panel_show(orb, session, page):
    await orb.post("/panel", {"name": "verify", "title": "verify",
                              "files": {"index.html": base64.b64encode(PANEL.encode()).decode()}})
    await page.wait_for_selector("#tabs >> text=verify", timeout=FLOW_TIMEOUT_S * 1000)
    await page.frame_locator("#panel-bodies iframe").locator("#go").wait_for(timeout=FLOW_TIMEOUT_S * 1000)


@flow("A click on a panel's button sends its request to the session, silently as the panel asked")
async def panel_send(orb, session, page):
    async def act():
        await page.frame_locator("#panel-bodies iframe").locator("#go").click()

    silent(await round_trip(orb, session, act, "from the verify panel"))


@flow("The mute key silences replies, and pressing it again brings speech back")
async def mute(orb, session, page):
    key = (await orb.health())["mute_key"]
    await focus_orb(page)
    await page.keyboard.press(key)
    await until(lambda: muted(orb), "the server to report speech muted")
    silent(await round_trip(orb, session, lambda: type_request(page, "asked while muted"), "asked while muted"))
    await focus_orb(page)
    await page.keyboard.press(key)
    await until(lambda: speaking_on(orb), "the server to report speech back on")


async def muted(orb) -> bool:
    return (await orb.health())["speak"] is False


async def speaking_on(orb) -> bool:
    return (await orb.health())["speak"] is True


@dataclass(frozen=True)
class Item:
    """One reliability check, owed whenever a change touches a path matching one of `when`."""
    id: str
    when: tuple[str, ...]
    flows: tuple[str, ...]
    check: str
    source: str


class ChecklistError(ValueError):
    pass


class BenchmarkError(ValueError):
    pass


def parse_checklist(data) -> list[Item]:
    if not isinstance(data, list):
        raise ChecklistError("the checklist is a JSON list of items")
    items = []
    for raw in data:
        try:
            item = Item(raw["id"], tuple(raw["when"]), tuple(raw.get("flows", ())), raw["check"], raw["source"])
        except (KeyError, TypeError) as e:
            raise ChecklistError(f"checklist item {raw!r} lacks {e}")
        unknown = [name for name in item.flows if name not in FLOWS]
        if unknown:
            raise ChecklistError(f"checklist item {item.id} names flows that do not exist: {', '.join(unknown)}")
        if not item.when:
            raise ChecklistError(f"checklist item {item.id} has no paths in `when`, so nothing would ever trigger it")
        items.append(item)
    return items


def triggered(items: list[Item], changed: list[str]) -> list[Item]:
    return [item for item in items if any(fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in item.when)]


def changed_paths(subject: Path, since: str) -> list[str]:
    """Every path that differs from `since`: committed on the branch, staged, unstaged, or new and untracked."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=subject, check=True, capture_output=True, text=True).stdout.split()
    base = git("merge-base", since, "HEAD")[0]
    return sorted(set(git("diff", "--name-only", base) + git("ls-files", "--others", "--exclude-standard")))


def flows_to_run(only: list[str], items: list[Item]) -> list[str]:
    if only:
        return only
    named = {name for item in items for name in item.flows}
    return [name for name, f in FLOWS.items() if f.runs == "always" or name in named]


@dataclass(frozen=True)
class Case:
    """A past fix the harness must tell apart: `flow` fails on the commit before `fix` and passes on it."""
    fix: str
    flow: str
    source: str


def parse_benchmark(data) -> list[Case]:
    if not isinstance(data, list):
        raise BenchmarkError("the benchmark is a JSON list of cases")
    try:
        cases = [Case(raw["fix"], raw["flow"], raw["source"]) for raw in data]
    except (KeyError, TypeError) as e:
        raise BenchmarkError(f"a benchmark case lacks {e}")
    unknown = [case.flow for case in cases if case.flow not in FLOWS]
    if unknown:
        raise BenchmarkError(f"the benchmark names flows that do not exist: {', '.join(unknown)}")
    return cases


def verdict(before_passed: bool, after_passed: bool) -> str:
    if not before_passed and after_passed:
        return "caught"
    if before_passed:
        return "missed: the flow passes without the fix"
    return "broken: the flow fails even with the fix"


def run_case(case: Case, subject: Path, harness: Path, out: Path) -> tuple[str, dict]:
    """Runs the case's flow against a checkout of the commit before the fix and of the fix itself. Each checkout gets
    the subject's browser launcher, the one piece of harness that lives in the orb's tree."""
    passed = {}
    for label, commit in (("before", f"{case.fix}~1"), ("after", case.fix)):
        tree = out / f"{case.fix}-{label}" / "tree"
        subprocess.run(["git", "worktree", "add", "--detach", "-q", str(tree), commit], cwd=subject, check=True)
        try:
            shutil.copy(subject / "orb" / "headless.py", tree / "orb" / "headless.py")
            run = subprocess.run(["uv", "run", "--project", str(tree), "python", str(harness), "--subject", str(tree),
                                  "--only", case.flow, "--out", str(out / f"{case.fix}-{label}" / "evidence")],
                                 capture_output=True, text=True)
            passed[label] = run.returncode == 0
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=subject, check=True)
    return verdict(passed["before"], passed["after"]), passed


async def run(subject: Path, out: Path, names: list[str]) -> list[dict]:
    home, repo = out / "home", out / "repo"
    home.mkdir(parents=True)
    repo.mkdir()
    orb = Orb(subject, home, repo)
    orb.configure()
    session = FakeSession(subject, home, repo)
    await session.start(orb)
    results = []
    try:
        await orb.start_server()
        async with async_playwright() as p:
            try:
                browser = await orb.headless.launch(p)
            except orb.headless.BrowserMissing as e:
                raise SystemExit(str(e))
            context = await browser.new_context(viewport={"width": 1400, "height": 900})
            await context.tracing.start(screenshots=True, snapshots=True)
            shared = await open_window(context, orb, out)
            for number, name in enumerate(names, 1):
                f = FLOWS[name]
                page = shared if f.page == "shared" else await open_window(context, orb, out)
                results.append(await run_flow(number, f, orb, session, page, out))
                if page is not shared:
                    await page.close()
            await context.tracing.stop(path=out / "trace.zip")
            await browser.close()
    finally:
        orb.stop_server()
        await session.stop()
        if orb.log_file().exists():
            (out / "orb.log").write_text(orb.log_file().read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return results


async def open_window(context, orb: Orb, out: Path):
    page = await context.new_page()
    orb.watch(page, out / "console.log")
    await page.goto(f"{orb.base}/?token={orb.token}")
    return page


async def run_flow(number: int, f: Flow, orb, session, page, out) -> dict:
    started = time.monotonic()
    error = None
    try:
        await f.fn(orb, session, page)
    except Failed as e:
        error = str(e)
    except Exception:
        error = traceback.format_exc(limit=3)
    shot = out / f"{number:02d}-{f.name}.png"
    await page.screenshot(path=shot)
    notice = await page.locator("#notice").inner_text()
    if error and notice:
        error += f"\nthe page's notice said: {notice}"
    return {"flow": f.name, "does": f.description, "ok": error is None, "error": error, "shortcut": f.shortcut,
            "seconds": round(time.monotonic() - started, 1), "screenshot": shot.name}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def print_flows() -> None:
    for name, f in FLOWS.items():
        tag = "" if f.runs == "always" else " (on demand: when named or a checklist item calls for it)"
        print(f"{name:26} {f.description}{tag}")
        if f.shortcut:
            print(f"{'':26} shortcut: {f.shortcut}")


def benchmark(subject: Path, out: Path) -> int:
    cases = parse_benchmark(load(HERE / "benchmark.json"))
    misses = 0
    for case in cases:
        result, passed = run_case(case, subject, Path(__file__).resolve(), out)
        misses += result != "caught"
        print(f"{result:44} {case.fix} {case.flow:26} {case.source}  (before: {'pass' if passed['before'] else 'fail'},"
              f" after: {'pass' if passed['after'] else 'fail'})")
    print(f"\n{len(cases) - misses}/{len(cases)} past fixes caught. Evidence: {out}")
    return 1 if misses else 0


def verify(subject: Path, out: Path, only: list[str], since: str | None) -> int:
    items = triggered(parse_checklist(load(HERE / "checklist.json")), changed_paths(subject, since)) if since else []
    results = asyncio.run(run(subject, out, flows_to_run(only, items)))
    report = {"flows": results, "checklist": [item.__dict__ for item in items]}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=list) + "\n", encoding="utf-8")
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['flow']:26} {r['seconds']:>5}s  {r['does']}")
        if r["shortcut"]:
            print(f"      shortcut: {r['shortcut']}")
        if not r["ok"]:
            print("      " + r["error"].strip().replace("\n", "\n      "))
    for item in items:
        print(f"\nCHECK {item.id} ({item.source}): {item.check}")
        if item.flows:
            print(f"      flows run for it: {', '.join(item.flows)}")
    failed = sum(not r["ok"] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} flows passed, {len(items)} checklist items triggered. Evidence: {out}")
    return 1 if failed or not results else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", type=Path, default=Path.cwd(), help="the agent-orb checkout under test")
    parser.add_argument("--out", type=Path, help="where the evidence goes; a new temporary folder by default")
    parser.add_argument("--only", nargs="+", choices=list(FLOWS), default=[], help="run only these flows")
    parser.add_argument("--changed-since", metavar="REF",
                        help="also run the checklist items whose paths the change since REF touches")
    parser.add_argument("--benchmark", action="store_true",
                        help="replay the past fixes in benchmark.json: each flow must fail before its fix and pass after")
    parser.add_argument("--list", action="store_true", help="print the feature map and exit")
    args = parser.parse_args()
    if args.list:
        print_flows()
        return 0
    out = (args.out or Path(tempfile.mkdtemp(prefix="orb-verify-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    subject = args.subject.resolve()
    if args.benchmark:
        return benchmark(subject, out)
    return verify(subject, out, args.only, args.changed_since)


if __name__ == "__main__":
    sys.exit(main())
