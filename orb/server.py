"""The orb server: serves the page, holds its WebSocket, runs speech-to-text and text-to-speech, relays requests
to the attached agent and takes replies back from it. Binds 127.0.0.1 only; every request and the socket must
present the per-launch token, with the exceptions `guard` lists; a foreign Origin is refused. Started by `orb open`
as `python -m orb.server ...`, which writes the token on its standard input; loads the engines, then binds and
writes server.<agent>.json."""
import argparse
import asyncio
import base64
import hashlib
import hmac
import html as html_text
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import struct
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web

from orb import PLUGIN_ROOT, actions, agents, engines, feeds, logs, pages, panels, skins, themes, widgets
from orb import interrupt as interrupt_adapter
from orb.audio import SAMPLE_RATE, TAP_S, speakable, split_sentences
from orb.side import side_intent, status_sentence
from orb.config import (WIDGET_PANE_KEYS, WIDGET_PLACE_KEYS, ConfigError, WidgetEntry, clamp_size, clamp_speed, copilot_port_for, engine_settings, expand_dirs, load_for,
                        overlay, save_for_repo, save_skin_size, save_speed, save_widget, size_for, speed_for, tts_settings_for, validate_widget_entry,
                        widgets_on)
from orb import waking
from orb.engines import EngineError, Stt, Tts, Voiceprint
from orb.wake import ENROLL_PHRASES, voiceprint_from
from orb.background import claude_running_tasks, same
from orb.feed import claude_busy, claude_doing, claude_feed, interrupted_since, newest_final, turn_since, unread, undelivered_final
from orb.gate import custom_title, read_transcript_from
from orb.state import port_file, read_json, read_turn, server_file, session_file, state_dir, write_json

WEB = PLUGIN_ROOT / "web"
SERVER_STATES = ("idle", "wake_word", "listening", "transcribing", "thinking", "speaking")
ATTACH_RECHECK_S = 5
SESSION_POLL_S = 0.5  # Claude Code writes the transcript once per finished content block, a median 2 s apart
PAGE_POLL_S = 2
WAIT_POLL_S = 1
WAIT_STEADY_POLLS = 3  # a step must outlast this many polls before the orb says it is waiting on it
PLAYBACK_GRACE_S = 5
WAKE_RETRY_S = 60.0
INTERRUPT_SETTLE_S = 3.0
BASE64_BLOCK = 3
JSON_CHAR_MAX_BYTES = len(json.dumps("\U0010ffff")) - len(json.dumps(""))
JSON_ENTRY_BYTES = len(json.dumps({"": "", "x": ""})) - len(json.dumps({"x": ""}))


def base64_length(size: int) -> int:
    return 4 * math.ceil(size / BASE64_BLOCK)


def files_base64_max(total: int, count: int) -> int:
    return base64_length(total + (BASE64_BLOCK - 1) * count)


PANEL_POST_MAX_BYTES = (len(json.dumps(panels.post_body("n" * pages.MAX_NAME_CHARS, "", {}, "n" * pages.MAX_NAME_CHARS)))
                        + panels.MAX_TITLE_CHARS * JSON_CHAR_MAX_BYTES
                        + JSON_ENTRY_BYTES + len("index.html")
                        + (pages.MAX_FILES - 1) * (JSON_ENTRY_BYTES + pages.MAX_PATH_CHARS * JSON_CHAR_MAX_BYTES)
                        + files_base64_max(pages.MAX_TOTAL_BYTES, pages.MAX_FILES))
ACTIONS_AT_ONCE = 4  # per panel; a panel that fires more waits for an answer first
WINDOW_GRACE_S = float(os.environ.get("ORB_WINDOW_GRACE_S", 20))  # a server with no window this long exits: closing the window ends the orb; covers a reload
log = logging.getLogger("orb")


class ClaudeLink:
    name = "Claude Code"

    def __init__(self, state: Path):
        self.state = state
        self.info = None
        self.session_name: str | None = None
        self.session_id: str | None = None
        self.transcript: list[dict] | None = None
        self.signature = None
        self.offset = 0
        self.shown = None  # (signature, feed, doing) as last built, so a poll that finds no new write builds nothing

    def refresh(self) -> None:
        """Reads what the transcript gained since the last look; the whole file only the first time, or when it
        was replaced or shrank. A long session's transcript is megabytes."""
        record = read_json(session_file(self.state)) or {}
        path = record.get("transcriptPath")
        self.session_id = record.get("sessionId")
        try:
            stat = Path(path).stat() if path else None
        except OSError:
            stat = None
        signature = (path, stat and stat.st_mtime_ns, stat and stat.st_size)
        if signature == self.signature and self.transcript is not None:
            return
        appended = (self.transcript is not None and stat is not None and path == (self.signature or (None,))[0]
                    and self.offset <= stat.st_size)
        read = read_transcript_from(path, self.offset if appended else 0)
        self.signature = signature
        if read is None:
            self.transcript, self.offset = None, 0
        else:
            entries, self.offset = read
            self.transcript = self.transcript + entries if appended else entries
        self.session_name = custom_title(self.transcript)

    def probe(self) -> bool:
        self.info = agents.read_channel(self.state)
        self.refresh()
        return agents.channel_bound(self.info)

    def activity(self) -> dict:
        self.refresh()
        if self.shown is None or self.shown[0] != self.signature:
            self.shown = (self.signature, claude_feed(self.transcript), claude_doing(self.transcript))
        _, feed, doing = self.shown
        return {"busy": self.busy(), "doing": doing, "feed": feed, "since": turn_since(feed),
                "session": self.signature and self.signature[0]}

    def busy(self) -> bool:
        """From the turn the plugin's hooks recorded, or from the transcript for a session they have not reached."""
        turn = read_turn(self.state, "claude", self.session_id)
        if turn is None:
            return claude_busy(self.transcript, now=datetime.now(timezone.utc))
        return turn.busy and not interrupted_since(self.transcript, turn.at)

    def background_tasks(self) -> list[dict]:
        return claude_running_tasks(self.transcript)

    def waiting(self) -> str | None:
        return None

    def doing(self) -> str | None:
        return claude_doing(self.transcript)

    def send(self, text: str, request_id: str) -> None:
        if not self.info:
            raise agents.LinkError("no channel for this repo; " + agents.restart_command())
        agents.post_prompt(self.info, text, request_id)

    def describe(self, attached: bool) -> str:
        if not attached:
            return "Not attached: start Claude Code in this repo with " + agents.restart_command()
        return f"Attached: Claude Code · {self.session_name}" if self.session_name else "Attached: Claude Code"

    def interrupt(self) -> agents.Stopped:
        target = interrupt_adapter.target_for(self.info.session_pid if self.info else None)
        if not target:
            log.warning("interrupt: no session process recorded by the channel")
            return agents.Stopped.STILL_WAITING
        try:
            return agents.Stopped.ABORTED if interrupt_adapter.send_escape(target) else agents.Stopped.STILL_WAITING
        except interrupt_adapter.NoAdapter as e:
            log.warning("interrupt: %s", e)
            return agents.Stopped.STILL_WAITING


def audio_frame(samples: np.ndarray, rate: int) -> bytes:
    """One rendered sentence for the page: little-endian uint32 sample rate, then float32 PCM."""
    return struct.pack("<I", rate) + np.asarray(samples, dtype="<f4").tobytes()


@dataclass(frozen=True)
class Request:
    """One request for the agent: speech (`spoken`, which the window is told it heard and which is joined with the
    speech queued next to it), or a panel's `orb.send` (`ref` and `sender`)."""
    text: str
    speak: bool = True
    ref: object = None
    woken: bool = False
    sender: str | None = None
    spoken: bool = False

    def joined(self, later: "Request") -> "Request":
        return replace(self, text=f"{self.text}\n{later.text}", woken=self.woken or later.woken)


class OrbServer:
    def __init__(self, repo_root: Path, agent: str, port: int, token: str, home: Path, link, stt=None, tts=None,
                 waker=None, wake_off: str | None = None):
        self.repo_root, self.agent, self.port, self.token, self.home, self.link = repo_root, agent, port, token, home, link
        self.config = load_for(repo_root, home)
        self.stt, self.tts = stt, tts
        self.waker, self.wake_off = waker, wake_off
        self.wake_lock = asyncio.Lock()  # two windows may both stream frames; the waker takes them one at a time
        self.wake_restarted_at: float | None = None
        self.interrupted_at: float | None = None
        self.voice_menu: dict | None = None  # the tts engine's voices, when it offers a choice
        self.enrolling: dict | None = None
        self.enroll_engine: Voiceprint | None = None
        self.state, self.said = "idle", None
        self.attached = False
        self.attach_text: str | None = None
        self.waiting: str | None = None  # the step the agent is in the middle of while the orb's request waits
        self.background: list[dict] = []  # what the attached agent is running in the background
        self.pending: dict[str, dict] = {}
        self.queue: list[Request] = []
        self.transcript: list[dict] = []
        self.sockets: set[web.WebSocketResponse] = set()
        self.session = {"busy": False, "doing": None, "feed": [], "unread": 0}
        self.seen_id = None
        self.seen_set = False
        self.session_key = None  # the session the feed is read from; another one starts a new first look
        self.heard: set = set()  # replies the user does not need spoken: spoken already, or there at the first look
        self.last_said: str | None = None  # the newest reply, a request's or the session's, for "repeat that"
        self.empty_since: float | None = time.monotonic()
        self.stopping = False
        self.recording: list[np.ndarray] | None = None
        self.speaking: asyncio.Task | None = None
        self.tasks: set[asyncio.Task] = set()
        self.played = asyncio.Event()
        self.reported_tts_failure: str | None = None
        # Panels are served under an unguessable prefix that is not the token, so a panel never holds the token
        # and the sandboxed iframe (opaque origin) cannot call anything else here.
        self.page_key = secrets.token_bytes(32)
        self.panels: dict[str, dict] = {}
        self.widgets: dict[str, dict] = {}
        self.widget_entries: dict[str, WidgetEntry] = dict(self.config["widgets"])
        self.widgets_missing: dict[str, str] = {}
        self.feed_runs: dict[str, dict] = {}
        self.action_runs: dict[str, int] = {}
        self.voice = bool(self.config["speak"])
        self.skin_dirs = expand_dirs(self.config["skin_dirs"], home)
        self.skin = skins.select(self.config["skin"], home, self.skin_dirs)
        self.skin_values = skins.option_values(self.skin, self.config["skin_options"].get(self.skin.name, {}))
        self.theme_dirs = expand_dirs(self.config["theme_dirs"], home)
        self.theme = themes.select(self.config["theme"], home, self.theme_dirs)
        self.theme_note: str | None = None
        self.app = web.Application(middlewares=[self.guard], client_max_size=PANEL_POST_MAX_BYTES)
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/pending", self.pending_ids)
        self.app.router.add_post("/reply", self.reply)
        self.app.router.add_post("/panel", self.panel_show)
        self.app.router.add_post("/panel/close", self.panel_close)
        self.app.router.add_post("/widget", self.widget_endpoint)
        self.app.router.add_post("/stop", self.stop_server)
        self.app.router.add_post("/voice", self.voice_endpoint)
        self.app.router.add_post("/enroll", self.enroll_endpoint)
        self.app.router.add_get("/skin/{path:.+}", self.skin_file)
        self.app.router.add_get("/theme/{path:.+}", self.theme_file)
        self.app.router.add_get("/ws", self.websocket)
        self.app.router.add_get("/p/{secret}/{name}/{path:.*}", self.panel_file)
        self.app.router.add_get("/w/{secret}/{name}/{path:.*}", self.widget_file)
        self.app.router.add_static("/static", WEB, show_index=False)

    # ---- access ----

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def own_page_asset(self, request: web.Request) -> bool:
        """A skin's module imports and a theme's fonts and images carry no token; the browser marks them as coming
        from this page."""
        return request.path.startswith(("/skin/", "/theme/")) and request.headers.get("Sec-Fetch-Site") == "same-origin"

    def page_secret(self, kind: str, name: str) -> str:
        return hmac.new(self.page_key, f"{kind}/{name}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    @property
    def shared_base(self) -> str:
        return f"/p/{self.page_secret('p', '_orb')}/_orb/"

    def panel_theme_path(self, request: web.Request) -> bool:
        return request.path.startswith(f"{self.shared_base}theme/")

    def page_path(self, request: web.Request) -> bool:
        return request.path.startswith(("/p/", "/w/"))

    def token_ok(self, request: web.Request) -> bool:
        given = request.headers.get("X-Orb-Token") or request.query.get("token") or ""
        return secrets.compare_digest(given.encode(), self.token.encode())

    @web.middleware
    async def guard(self, request: web.Request, handler):
        origin = request.headers.get("Origin")
        # A font a theme's panels.css names is a CORS fetch, and a sandboxed panel's origin is opaque: "null".
        if origin is not None and origin != self.origin and not (origin == "null" and self.panel_theme_path(request)):
            raise web.HTTPForbidden(text="foreign origin")
        if not self.page_path(request) and not self.token_ok(request) and not self.own_page_asset(request):
            raise web.HTTPForbidden(text="missing or wrong token")
        response = await handler(request)
        # Every file here is read from disk on each request and edited in place; a cached orb.js under a new
        # index.html is a page that half works.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # ---- panels and skin ----

    async def panel_show(self, request: web.Request) -> web.Response:
        """Shows a page from the files posted, or a saved panel named by `saved`, which the server reads from its own
        folder so it can reload it when its files change. A CLI sends both; a server older than `saved` uses the files."""
        body = await request.json()
        try:
            name = panels.valid_name(str(body.get("name", "")))
            source = None
            if body.get("saved"):
                source = panels.find_saved(str(body["saved"]), self.repo_root, self.home)
                if source is None:
                    raise pages.PageError(f"no saved panel named {body['saved']!r}")
                files = await asyncio.to_thread(panels.collect_files, source.path / "index.html")
            else:
                files = {path: base64.b64decode(data) for path, data in (body.get("files") or {}).items()}
        except pages.PageError as e:
            raise web.HTTPBadRequest(text=str(e))
        if name not in panels.BUILTIN and "index.html" not in files:
            raise web.HTTPBadRequest(text="a panel needs an index.html")
        title = str(body.get("title") or (source.title if source else name))
        self.drop_panel(name)
        self.panels[name] = self.page_entry(files, source) | {"title": title}
        await self.broadcast(self.panel_message(name))
        return web.json_response({"ok": True, "name": name, "url": self.panel_url(name)})

    @staticmethod
    def page_entry(files: dict[str, bytes], source) -> dict:
        return {"files": files, "source": source, "signature": pages.signature(source.path) if source else None,
                "page_error": None, "feed_error": None, "data": None, "data_at": None, "data_stamp": None}

    def open_pages(self) -> dict[str, dict[str, dict]]:
        return {"panel": self.panels, "widget": self.widgets}

    async def panel_close(self, request: web.Request) -> web.Response:
        name = str((await request.json()).get("name", ""))
        if name not in self.panels:
            raise web.HTTPNotFound(text=f"no open panel named {name!r}")
        self.drop_panel(name)
        await self.broadcast({"type": "panel_close", "name": name})
        return web.json_response({"ok": True})

    def drop_panel(self, name: str) -> None:
        self.drop_page(self.panels, name)

    def drop_page(self, open_pages: dict[str, dict], name: str) -> None:
        open_pages.pop(name, None)
        if any(name in pages_of_kind for pages_of_kind in self.open_pages().values()):
            return
        run = self.feed_runs.pop(name, {}).get("run")
        if run:
            run.cancel()

    async def watch_pages(self) -> None:
        previous: dict[tuple[str, str], tuple] = {}
        while True:
            try:
                await self.retry_missing_widgets()
            except Exception:
                log.exception("missing widgets")
            for kind, open_pages in self.open_pages().items():
                for name, page in list(open_pages.items()):
                    try:
                        if page["source"]:
                            now = await asyncio.to_thread(pages.signature, page["source"].path)
                            if pages.settled(previous.get((kind, name)), now, page["signature"]):
                                await self.reload_page(kind, name, page, now)
                            previous[(kind, name)] = now
                        await self.tend_feed(kind, name, page)
                    except Exception as e:
                        log.exception("%s %s", kind, name)
                        await self.feed_status(kind, name, page, f"stopped: {e!r}")
            await asyncio.sleep(PAGE_POLL_S)

    async def reload_page(self, kind: str, name: str, page: dict, signature: tuple) -> None:
        page["signature"] = signature
        folder = page["source"].path
        try:
            if kind == "panel":
                await asyncio.to_thread(panels.read_manifest, folder)
            else:
                page["source"] = await asyncio.to_thread(widgets.read_manifest, folder, page["source"].scope)
            page["files"] = await asyncio.to_thread(pages.collect_files, folder / "index.html")
        except (pages.PageError, OSError) as e:
            await self.page_status(kind, name, page, f"kept the last page; {e}")
            return
        if self.open_pages()[kind].get(name) is page:
            await self.page_status(kind, name, page, None)
            await self.broadcast(self.page_message(kind, name) | {"in_place": True})

    async def page_status(self, kind: str, name: str, page: dict, error: str | None) -> None:
        before, page["page_error"] = page["page_error"], error
        if before == error:
            return
        await self.broadcast({"type": "page_status", "kind": kind, "name": name, "error": error})
        if error and not before:
            await self.notify("error", f"{kind} {name}: {error}")

    def page_message(self, kind: str, name: str) -> dict:
        return self.panel_message(name) if kind == "panel" else self.widget_message(name)

    async def tend_feed(self, kind: str, name: str, page: dict) -> None:
        """Runs the page's feed when its output is older than `every` and it has not been tried for as long, and
        hands the page the output whenever it changes, whoever wrote it."""
        open_pages = self.open_pages()[kind]
        if open_pages.get(name) is not page:
            return
        try:
            feed = feeds.read_feed(self.home, self.repo_root, name)
        except feeds.FeedError as e:
            await self.feed_status(kind, name, page, str(e))
            return
        if feed is None:
            await self.feed_status(kind, name, page, None)
            return
        now = time.time()
        output = self.feed_output(feed)
        runs = self.feed_runs.setdefault(name, {"run": None, "tried": 0.0})
        idle = not runs["run"] or runs["run"].done()
        if idle and feeds.output_age(output, now) >= feed.every and now - runs["tried"] >= feed.every:
            runs["tried"] = now
            runs["run"] = self.spawn(self.run_feed(name, feed))
        stamp = feeds.output_stamp(output)
        if not stamp or stamp == page["data_stamp"]:
            return
        page["data_stamp"] = stamp
        try:
            data = await asyncio.to_thread(feeds.read_output, output)
        except (feeds.FeedError, OSError) as e:
            await self.feed_status(kind, name, page, f"kept the last data; {e}")
            return
        if open_pages.get(name) is not page:
            return
        page["data"], page["data_at"] = data, stamp[0] / 1e9
        await self.broadcast({"type": f"{kind}_data", "name": name, "data": data, "at": page["data_at"]})
        await self.feed_status(kind, name, page, None)

    def orb_env(self) -> dict[str, str]:
        return feeds.orb_env(self.repo_root, self.agent, self.link.session_id)

    def own_feed_output(self, feed: feeds.Feed) -> Path:
        folder = state_dir(self.home, self.repo_root) / "feeds"
        folder.mkdir(exist_ok=True)
        return folder / f"{self.agent}.{feed.name}.json"

    def feed_output(self, feed: feeds.Feed) -> Path:
        """The file this orb reads the feed from: whichever of its own and the shared output the feed wrote last, so a
        feed that changes which one it writes is followed."""
        own, shared = self.own_feed_output(feed), feed.output_path
        stamps = {path: feeds.output_stamp(path) for path in (own, shared)}
        written = [path for path, stamp in stamps.items() if stamp]
        return max(written, key=lambda path: stamps[path][0]) if written else shared

    async def run_feed(self, name: str, feed: feeds.Feed) -> None:
        failed = await feeds.run(feed, self.orb_env(), self.own_feed_output(feed))
        if failed:
            for kind, open_pages in self.open_pages().items():
                if name in open_pages:
                    await self.feed_status(kind, name, open_pages[name], f"refresh failed; {failed}")

    async def page_action(self, message: dict) -> None:
        """Runs one of the user's actions for an open panel or widget and sends back its answer. The page says
        whether the request came with a click, which the browser lets it see in the page's frame; an action that
        needs one is refused without it."""
        ref, page, name = message.get("ref"), str(message.get("page", "")), str(message.get("name", ""))
        kind = "widget" if message.get("kind") == "widget" else "panel"

        async def answer(ok: bool, value) -> None:
            await self.broadcast({"type": "action_result", "ref": ref, "ok": ok, **({"result": value} if ok else {"error": value})})

        if page not in self.open_pages()[kind]:
            return await answer(False, f"no {kind} named {page!r} is open")
        try:
            found = await asyncio.to_thread(actions.read_actions, self.home, self.repo_root, page)
        except actions.ActionError as e:
            return await answer(False, str(e))
        action = found.get(name)
        if action is None:
            return await answer(False, f"the {page} {kind} has no action {name!r}; actions live in "
                                       f"{actions.actions_dirs(self.home, self.repo_root)[0] / page}/actions.json")
        if action.click and not message.get("active"):
            return await answer(False, f"{name} works only during a click or key press")
        if self.action_runs.get(page, 0) >= ACTIONS_AT_ONCE:
            return await answer(False, "busy")
        self.action_runs[page] = self.action_runs.get(page, 0) + 1
        log.info("action %s/%s", page, name)
        try:
            result = await actions.run(action, message.get("args"), self.orb_env())
        except actions.ActionError as e:
            log.warning("action %s/%s failed: %s", page, name, e)
            return await answer(False, str(e))
        finally:
            self.action_runs[page] -= 1
        await answer(True, result)

    async def feed_status(self, kind: str, name: str, page: dict, error: str | None) -> None:
        """Marks the page from a failure until new data arrives or the feed is removed, and says why when it first
        fails; while it stays failing, the mark carries the latest reason."""
        before, page["feed_error"] = page["feed_error"], error
        if before == error:
            return
        await self.broadcast({"type": "feed_status", "kind": kind, "name": name, "error": error})
        if error and not before:
            await self.notify("error", f"{kind} {name}: {error}")

    def panel_url(self, name: str) -> str:
        return f"/p/{self.page_secret('p', name)}/{name}/index.html"

    def panel_message(self, name: str) -> dict:
        panel = self.panels[name]
        return {"type": "panel", "name": name, "title": panel["title"], "url": self.panel_url(name),
                "builtin": name in panels.BUILTIN, "data": panel["data"], "data_at": panel["data_at"],
                "feed_error": panel["feed_error"]}

    async def widget_endpoint(self, request: web.Request) -> web.Response:
        body = await request.json()
        name, on = str(body.get("name", "")), bool(body.get("on"))
        problem = await (self.widget_on(name) if on else self.widget_off(name))
        if problem:
            raise web.HTTPNotFound(text=problem)
        return web.json_response({"ok": True, "name": name, "on": on})

    async def widget_on(self, name: str) -> str | None:
        problem = await self.show_widget(name)
        if problem is None:
            self.widgets_missing.pop(name, None)
            self.remember_widget(name, {"on": True})
            await self.broadcast_widgets_installed()
        return problem

    async def show_widget(self, name: str) -> str | None:
        try:
            widget = await asyncio.to_thread(widgets.find, name, self.repo_root, self.home)
            if widget is None:
                return f"no widget named {name!r} is installed (orb widgets list shows those)"
            files = await asyncio.to_thread(pages.collect_files, widget.path / "index.html")
        except (pages.PageError, OSError) as e:
            return str(e)
        self.drop_page(self.widgets, name)
        self.widgets[name] = self.page_entry(files, widget)
        await self.broadcast(self.widget_message(name))
        return None

    async def widget_off(self, name: str) -> str | None:
        if name not in self.widgets and name not in self.widget_entries:
            try:
                installed = await asyncio.to_thread(widgets.find, name, self.repo_root, self.home) is not None
            except pages.PageError as e:
                return str(e)
            if not installed:
                return f"no widget named {name!r} is installed or on"
        self.drop_page(self.widgets, name)
        self.widgets_missing.pop(name, None)
        self.remember_widget(name, {"on": False})
        await self.broadcast({"type": "widget_close", "name": name})
        await self.broadcast_widgets_installed()
        return None

    def remember_widget(self, name: str, entry: WidgetEntry) -> None:
        self.widget_entries[name] = {**self.widget_entries.get(name, {}), **entry}
        save_widget(self.home, self.repo_root, name, entry)

    async def place_widget(self, name: str, place: dict) -> None:
        if name not in self.widgets:
            return
        try:
            kept = {key: float(place[key]) for key in (*WIDGET_PLACE_KEYS, *(k for k in WIDGET_PANE_KEYS if k in place))}
            validate_widget_entry(kept, f"widget {name} place")
        except (KeyError, TypeError, ValueError, OverflowError, ConfigError) as e:
            log.warning("widget %s: place refused: %s", name, e)
            return
        self.remember_widget(name, kept)
        await self.broadcast({"type": "widget_place", "name": name, **kept})

    async def show_saved_widgets(self) -> None:
        for name in sorted(widgets_on(self.widget_entries)):
            problem = await self.show_widget(name)
            if problem:
                self.widgets_missing[name] = problem
                log.warning("widget %s: %s", name, problem)

    async def retry_missing_widgets(self) -> None:
        """A widget on in the config that could not be shown at start is tried again on every poll, so a folder
        that arrives later shows without a restart."""
        for name in list(self.widgets_missing):
            problem = await self.show_widget(name)
            if name not in self.widgets_missing:
                if problem is None and not self.widget_entries.get(name, {}).get("on"):
                    self.drop_page(self.widgets, name)
                    await self.broadcast({"type": "widget_close", "name": name})
                continue
            if problem is None:
                del self.widgets_missing[name]
            elif problem == self.widgets_missing[name]:
                continue
            else:
                self.widgets_missing[name] = problem
            await self.broadcast_widgets_installed()

    async def broadcast_widgets_installed(self) -> None:
        await self.broadcast({"type": "widgets_installed", **self.widgets_installed()})

    def widgets_installed(self) -> dict:
        try:
            installed, _ = widgets.list_installed(self.repo_root, self.home)
        except pages.PageError:
            installed = []
        first = {}
        for w in installed:
            if w.name not in self.widgets_missing:
                first.setdefault(w.name, w)
        return {"installed": [w.describe() for w in first.values()],
                "missing": [{"name": name, "reason": reason} for name, reason in self.widgets_missing.items()]}

    def widget_url(self, name: str) -> str:
        return f"/w/{self.page_secret('w', name)}/{name}/index.html"

    def widget_message(self, name: str) -> dict:
        page = self.widgets[name]
        widget, saved = page["source"], self.widget_entries.get(name, {})
        return {"type": "widget", "name": name, "url": self.widget_url(name), "description": widget.description,
                "corner": widget.corner, "x": saved.get("x"), "y": saved.get("y"),
                "width": saved.get("width", widget.width), "height": saved.get("height", widget.height),
                "pane_width": saved.get("pane_width"), "pane_height": saved.get("pane_height"),
                "data": page["data"], "data_at": page["data_at"], "feed_error": page["feed_error"],
                "page_error": page["page_error"]}

    def widgets_snapshot(self) -> dict:
        return self.widgets_installed() | {"shown": [self.widget_message(n) for n in self.widgets]}

    async def widget_file(self, request: web.Request) -> web.Response:
        return self.page_file(request, "w", self.widgets)

    async def panel_file(self, request: web.Request) -> web.Response:
        return self.page_file(request, "p", self.panels)

    def page_file(self, request: web.Request, kind: str, open_pages: dict[str, dict]) -> web.Response:
        name, path = request.match_info["name"], request.match_info["path"]
        if not secrets.compare_digest(request.match_info["secret"].encode(), self.page_secret(kind, name).encode()):
            raise web.HTTPForbidden(text="unknown page path")
        if name == "_orb":
            if path.startswith("theme/"):
                found = themes.theme_file(self.theme, path.removeprefix("theme/"))
                if found is None:
                    raise web.HTTPNotFound(text="not in the theme's folder")
                return web.FileResponse(found, headers={"Access-Control-Allow-Origin": "*"})
            if path not in ("panel.js", "panel.css"):
                raise web.HTTPNotFound()
            return web.FileResponse(WEB / path)
        page = open_pages.get(name)
        if not page or path not in page["files"]:
            raise web.HTTPNotFound(text="no such page file")
        data = page["files"][path]
        if path == "index.html":
            return web.Response(text=self.inject_panel_api(data.decode("utf-8", "replace")), content_type="text/html",
                                headers={"Cache-Control": "no-store"})
        return web.Response(body=data, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")

    def inject_panel_api(self, html: str) -> str:
        base = self.shared_base
        tags = (f'<link rel="stylesheet" href="{base}panel.css">' + self.theme_tags(f"{base}theme/panels.css", "panels.css")
                + f'<script src="{base}panel.js"></script>')
        match = re.search(r"<head[^>]*>", html, re.I)
        if match:
            return html[:match.end()] + tags + html[match.end():]
        return tags + html

    def theme_tags(self, href: str, sheet: str) -> str:
        """After a page's default stylesheet: the theme's tokens, then its own sheet for that page, so both win over
        the default and anything the page itself styles later wins over them."""
        css = themes.token_css(self.theme)
        tags = f'<style id="orb-theme">{css}</style>' if css else ""
        if sheet in self.theme.sheets:
            tags += f'<link rel="stylesheet" href="{href}">'
        return tags

    async def theme_file(self, request: web.Request) -> web.Response:
        path = themes.theme_file(self.theme, request.match_info["path"])
        if path is None:
            raise web.HTTPNotFound(text="not in the theme's folder")
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    async def skin_file(self, request: web.Request) -> web.Response:
        path = skins.skin_file(self.skin, request.match_info["path"])
        if path is None:
            raise web.HTTPNotFound(text="not in the skin's folder")
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    async def set_tts_engine(self, name: str) -> None:
        """Switches this repo's orb to another installed tts engine, saved for this repo. Speech in progress stops;
        an engine that fails to load leaves the current one speaking and says why."""
        if self.tts is None or name == self.tts.name or name not in await asyncio.to_thread(engines.available, "tts", self.home):
            return
        await self.notify("info", f"switching the voice to {name}…")
        failed = await self.use_tts(self.config["tts"] | {"engine": name})
        if failed:
            await self.notify("error", f"voice {name}: {failed}")
            return

        def update(data: dict) -> None:
            saved = data.get("tts") if isinstance(data.get("tts"), dict) else {}
            data["tts"] = {**saved, "engine": name}

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        log.info("speech engine switched to %s", name)
        await self.broadcast({"type": "engines", "engines": self.engines_snapshot(), "voices": self.voice_menu})

    async def set_stt_engine(self, name: str) -> None:
        """Switches this repo's orb to another installed stt engine, saved for this repo. A clip being transcribed
        finishes on the old engine; an engine that fails to load leaves the current one listening and says why."""
        if self.stt is None or name == self.stt.name or name not in await asyncio.to_thread(engines.available, "stt", self.home):
            return
        await self.notify("info", f"switching transcription to {name}…")
        failed = await self.use_stt(self.config["stt"] | {"engine": name})
        if failed:
            await self.notify("error", f"transcription {name}: {failed}")
            return

        def update(data: dict) -> None:
            saved = data.get("stt") if isinstance(data.get("stt"), dict) else {}
            data["stt"] = {**saved, "engine": name}

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        log.info("transcription engine switched to %s", name)
        await self.broadcast({"type": "engines", "engines": self.engines_snapshot(), "voices": self.voice_menu})

    async def use_stt(self, settings: dict) -> str | None:
        name = settings["engine"]
        try:
            stt = await asyncio.to_thread(Stt, lambda n: engine_settings(settings | ({"engine": n} if n else {})), self.home)
        except EngineError as e:
            return str(e)
        if stt.name != name:
            reason = stt.fallback_reason
            await asyncio.to_thread(stt.close)
            return reason
        while self.state == "transcribing":
            await asyncio.sleep(0.05)
        old, self.stt = self.stt, stt
        await asyncio.to_thread(old.close)
        self.config["stt"].update(settings)
        return None

    async def use_tts(self, settings: dict) -> str | None:
        name = settings["engine"]
        try:
            tts = await asyncio.to_thread(Tts, lambda n: tts_settings_for(settings | ({"engine": n} if n else {}), self.agent), self.home)
        except EngineError as e:
            return str(e)
        if tts.name != name:
            reason = tts.fallback_reason
            await asyncio.to_thread(tts.close)
            return reason
        tts.speed = self.speed
        await self.stop_speaking()
        if self.state == "speaking":
            await self.set_state(self.resume_state())
        old, self.tts = self.tts, tts
        await asyncio.to_thread(old.close)
        self.config["tts"].update(settings)
        self.voice_menu = await asyncio.to_thread(self.tts.voices)
        return None

    async def set_tts_voice(self, voice_id: str) -> None:
        """Switches the current engine's voice, saved in that engine's block for this repo, so each engine keeps its
        own voice."""
        menu = self.voice_menu
        if self.tts is None or menu is None or voice_id == menu["current"] or voice_id not in [v["id"] for v in menu["voices"]]:
            return
        try:
            await asyncio.to_thread(self.tts.set_voice, voice_id)
        except EngineError as e:
            await self.notify("error", f"voice: {e}")
            return
        menu["current"] = voice_id
        engine = self.tts.name
        log.info("voice for %s switched to %s", engine, voice_id)

        def update(data: dict) -> None:
            saved = data.get("tts") if isinstance(data.get("tts"), dict) else {}
            block = saved.get(engine) if isinstance(saved.get(engine), dict) else {}
            data["tts"] = {**saved, engine: {**block, "voice": voice_id}}

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        self.config["tts"][engine] = {**(self.config["tts"].get(engine) or {}), "voice": voice_id}
        await self.broadcast({"type": "voices", "voices": menu})

    def engines_snapshot(self) -> dict:
        return {kind: {"name": engine.name, "fallback_reason": engine.fallback_reason} if engine else None
                for kind, engine in (("stt", self.stt), ("tts", self.tts))}

    async def speak_text(self, text: str, said: str | None = None) -> None:
        """A panel's orb.speak or a transcript speaker: read the text aloud in the orb's voice, no agent turn. Only while
        idle, like a request."""
        if not text.strip() or self.state != "idle" or not self.tts:
            return
        if not self.voice:
            await self.notify("info", "the orb is muted")
            return
        log.info("the page asked to read %d chars aloud", len(text))
        self.last_said = text
        await self.set_state("speaking", said)
        self.speaking = asyncio.create_task(self.speak(text, said=said))

    async def stop_speaking(self) -> None:
        if self.speaking and not self.speaking.done():
            self.speaking.cancel()
            try:
                await self.speaking
            except asyncio.CancelledError:
                pass

    async def set_skin(self, name: str) -> None:
        """Switches this repo's orb to another installed skin, saved for this repo; every window reloads to draw it."""
        if name == self.skin.name or name not in await asyncio.to_thread(skins.available, self.home, self.skin_dirs):
            return
        self.skin = await asyncio.to_thread(skins.select, name, self.home, self.skin_dirs)
        log.info("skin switched to %s", self.skin.name)
        saved = self.config["skin_options"].get(self.skin.name, {})
        self.skin_values = skins.option_values(self.skin, saved)

        def update(data: dict) -> None:
            data["skin"] = self.skin.name

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        await self.broadcast({"type": "skin", "name": self.skin.name})

    async def set_theme(self, name: str) -> None:
        if name == self.theme.name or name not in await asyncio.to_thread(themes.available, self.home, self.theme_dirs):
            return
        self.theme = await asyncio.to_thread(themes.select, name, self.home, self.theme_dirs)
        notes = []
        wanted = self.theme.skin
        skin_switched = False
        if wanted and wanted != self.skin.name:
            if wanted in await asyncio.to_thread(skins.available, self.home, self.skin_dirs):
                self.skin = await asyncio.to_thread(skins.select, wanted, self.home, self.skin_dirs)
                skin_switched = True
                self.skin_values = skins.option_values(self.skin, self.config["skin_options"].get(self.skin.name, {}))
            else:
                notes.append(f"the {self.theme.name} theme names the {wanted} skin, which is not installed; "
                             f"keeping {self.skin.name}")
        speech = self.theme.tts if self.tts else None
        if speech:
            engine, settings, failed = speech["engine"], overlay("tts", self.config["tts"], speech), None
            if engine not in await asyncio.to_thread(engines.available, "tts", self.home):
                failed = "which is not installed"
            elif engine != self.tts.name or settings != self.config["tts"]:
                failed = await self.use_tts(settings)
                failed = failed and f"which did not load ({failed})"
            if failed:
                notes.append(f"the {self.theme.name} theme names the {engine} speech engine, {failed}; keeping {self.tts.name}")
                speech = None
        self.theme_note = "; ".join(notes) or None
        if self.theme_note:
            log.warning("theme: %s", self.theme_note)
        log.info("theme switched to %s (skin %s)", self.theme.name, self.skin.name)

        def update(data: dict) -> None:
            data["theme"] = self.theme.name
            if skin_switched:
                data["skin"] = self.skin.name
            if speech:
                data["tts"] = overlay("tts", data["tts"] if isinstance(data.get("tts"), dict) else {}, speech)

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        await self.broadcast({"type": "theme", "name": self.theme.name})

    async def set_skin_option(self, name: str, value: str) -> None:
        option = self.skin.options.get(name)
        if option is None or value not in option.choices:
            return
        self.skin_values[name] = value
        options = self.config["skin_options"]
        self.config.values["skin_options"] = {**options, self.skin.name: {**options.get(self.skin.name, {}), name: value}}

        def update(data: dict) -> None:
            saved = data.get("skin_options") if isinstance(data.get("skin_options"), dict) else {}
            mine = saved.get(self.skin.name) if isinstance(saved.get(self.skin.name), dict) else {}
            data["skin_options"] = {**saved, self.skin.name: {**mine, name: value}}

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        await self.broadcast({"type": "skin_option", "option": name, "value": value})

    async def set_skin_size(self, value: float) -> None:
        """Kept per skin in the user's config for this repo, so each skin comes back at the size it was left at."""
        size = clamp_size(value)
        self.config.values["skin_sizes"] = {**self.config["skin_sizes"], self.skin.name: size}
        await asyncio.to_thread(save_skin_size, self.home, self.repo_root, self.skin.name, size)
        await self.broadcast({"type": "skin_size", "value": size})

    # ---- page ----

    async def index(self, request: web.Request) -> web.Response:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        theme = self.theme_tags(f"/theme/orb.css?token={self.token}", "orb.css")
        escaped = {key: html_text.escape(value) for key, value in {"TOKEN": self.token, "REPO": self.repo_root.name, "AGENT": self.link.name}.items()}
        for key, value in {**escaped, "THEME": theme}.items():
            html = html.replace("{{" + key + "}}", value)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(self.snapshot() | {"ok": True, "pid": os.getpid(), "windows": len(self.sockets)})

    def snapshot(self) -> dict:
        return {"type": "snapshot", "state": self.state, "attached": self.attached, "agent": self.link.name,
                "attach_text": self.link.describe(self.attached), "repo": str(self.repo_root),
                "engines": self.engines_snapshot(), "tts_engines": engines.available("tts", self.home) if self.tts else [],
                "stt_engines": engines.available("stt", self.home) if self.stt else [],
                "voices": self.voice_menu,
                "voice": bool(self.stt and self.tts), "speak": self.voice, "said": self.said, "mute_key": self.config["mute_key"], "panels_key": self.config["panels_key"], "wake": self.wake_snapshot(), "enrolling": self.enroll_snapshot(), "ptt_key": self.config["ptt_key"], "speed": self.speed, "background": self.background, "waiting": self.waiting, "session": self.session,
                "skin": self.skin.describe() | {"values": self.skin_values, "size": size_for(self.config["skin_sizes"], self.skin.name)}, "skin_key": self.config["skin_key"],
                "skins": skins.available(self.home, self.skin_dirs),
                "theme": self.theme.describe() | {"note": self.theme_note}, "themes": themes.available(self.home, self.theme_dirs),
                "panels": [self.panel_message(name) for name in self.panels],
                "widgets": self.widgets_snapshot(),
                "transcript": self.transcript[-50:]}

    # ---- requests and replies ----

    async def pending_ids(self, request: web.Request) -> web.Response:
        return web.json_response({"pending": sorted(self.pending)})

    async def reply(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="JSON body expected")
        request_id, text = str(body.get("req", "")), str(body.get("text", ""))
        if request_id not in self.pending:
            raise web.HTTPNotFound(text="no such pending request")
        self.pending.pop(request_id)
        await self.deliver_reply(request_id, text)
        return web.json_response({"ok": True})

    async def watch_wait(self, request_id: str) -> None:
        """While the request is out, says what the agent is in the middle of whenever that holds for a few polls."""
        seen, steady = None, 0
        try:
            while request_id in self.pending:
                now = await asyncio.to_thread(self.link.waiting)
                steady = steady + 1 if now == seen else 1
                seen = now
                shown = now if steady >= WAIT_STEADY_POLLS else None
                if shown != self.waiting:
                    self.waiting = shown
                    await self.broadcast({"type": "waiting", "text": shown})
                await asyncio.sleep(WAIT_POLL_S)
        finally:
            if self.waiting is not None:
                self.waiting = None
                await self.broadcast({"type": "waiting", "text": None})

    async def watch_session(self) -> None:
        """The session's own busy state and feed, so a turn typed in the terminal shows here as it does for the
        orb's, and its reply is spoken. The first read of a session marks everything already in its feed as seen and
        heard: only replies from now on are spoken or missed."""
        while True:
            if self.attached:
                try:
                    activity = await asyncio.to_thread(self.link.activity)
                except (agents.LinkError, OSError, ValueError) as e:
                    log.warning("session activity: %s", e)
                    activity = None
                if activity is not None:
                    key = activity.get("session")
                    if not self.seen_set or key != self.session_key:
                        if self.seen_set:
                            await self.drop_queue(f"{self.link.name} is in another session now")
                        self.seen_id, self.seen_set, self.session_key = newest_final(activity["feed"]), True, key
                        self.heard = {item["id"] for item in activity["feed"] if undelivered_final(item)}
                    await self.speak_session_reply(activity["feed"])
                    await self.announce_session(activity)
            await asyncio.sleep(SESSION_POLL_S)

    async def speak_session_reply(self, feed: list[dict]) -> None:
        """Speaks the oldest reply no request delivered (a turn typed in the terminal, or one the agent started when
        a background task finished), one at a time and only while the orb is idle with a window to hear it, so it
        never talks over listening, a request or other speech. Stop cuts it off like any reply."""
        if not self.tts or not self.sockets or self.state != "idle":
            return
        reply = next((item for item in feed if undelivered_final(item) and item["id"] not in self.heard), None)
        if reply is None:
            return
        self.heard.add(reply["id"])
        self.last_said = reply["text"]
        if not self.voice:
            return  # muted: the reply stays missed until the window is looked at, and unmuting does not read it
        await self.set_state("speaking", reply["id"])
        self.speaking = asyncio.create_task(self.speak(reply["text"], said=reply["id"]))

    async def announce_session(self, activity: dict) -> None:
        session = {k: v for k, v in activity.items() if k != "session"}
        session["unread"] = len(unread(activity["feed"], self.seen_id))
        if session != self.session:
            previous = {item["id"]: item for item in self.session["feed"]}
            self.session = session
            await self.broadcast({"type": "session", **{k: v for k, v in session.items() if k != "feed"},
                                  "feed": [item for item in session["feed"] if previous.get(item["id"]) != item],
                                  "order": [item["id"] for item in session["feed"]]})
            if not session["busy"] and self.queue:
                self.spawn(self.send_queued())

    async def mark_seen(self) -> None:
        """The user looked at the window: whatever has finished is no longer missed."""
        self.seen_id = newest_final(self.session["feed"])
        await self.announce_session(self.session)

    async def announce_tasks(self) -> None:
        try:
            tasks = await asyncio.to_thread(self.link.background_tasks) if self.attached else []
        except agents.LinkError as e:
            log.warning("background tasks: %s", e)
            return
        if not same(tasks, self.background):
            self.background = tasks
            await self.broadcast({"type": "background", "tasks": tasks})

    async def announce_attachment(self) -> None:
        text = self.link.describe(self.attached)
        if text != self.attach_text:
            self.attach_text = text
            await self.broadcast({"type": "attached", "attached": self.attached, "text": text})

    async def deliver_reply(self, request_id: str, text: str) -> None:
        entry = self.entry(request_id)
        log.info("reply %s: %d chars", request_id, len(text))
        entry["reply"], entry["status"] = text, "answered"
        if text.strip():
            self.last_said = text
        await self.broadcast({"type": "reply", "req": request_id, "text": text, "ref": entry.get("ref")})
        if self.speaking and not self.speaking.done():
            self.speaking.cancel()
            try:
                await self.speaking
            except asyncio.CancelledError:
                pass
        if self.tts and self.voice and entry.get("speak", True) and text.strip():
            self.speaking = asyncio.create_task(self.speak(text, follow_up=entry.get("woken", False)))
        else:
            await self.set_state("idle")

    def entry(self, request_id: str) -> dict:
        return next(e for e in self.transcript if e.get("req") == request_id)

    @property
    def busy(self) -> bool:
        return self.state != "idle"

    async def ask(self, text: str, speak: bool = True, ref=None, woken: bool = False, sender: str | None = None) -> None:
        """Queues one request for the agent; blank text is never queued. `ref` is the page's own id for a panel's
        `orb.send`, echoed on the reply, or on the refusal of blank text, so the page can settle it; `sender` names
        that page, and the agent receives the text marked as the page's rather than as the user's words."""
        text = text.strip()
        if text:
            await self.submit(Request(text, speak, ref, woken, sender))
        elif ref is not None:
            await self.notify("info", "nothing to send: the text was empty", ref)

    def blocked(self) -> bool:
        return self.busy or self.session["busy"]

    def must_wait(self) -> bool:
        return self.blocked() or bool(self.queue)

    def ready(self) -> bool:
        """The follow-up window after a woken reply is the user's to speak in; what is queued goes after it."""
        follow_up = self.waker is not None and self.waker.follow_up.is_open(time.monotonic())
        return bool(self.queue) and not self.blocked() and bool(self.sockets) and not follow_up

    async def send_queued_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.send_queued()

    async def submit(self, request: "Request") -> None:
        """Every request joins the queue, and the one sender sends it when its turn comes: now, if nothing is in the
        way. Never into a running turn, where Claude Code declines it as not the user's (the spec's measurement)."""
        waiting = self.must_wait()
        self.queue.append(request)
        if waiting:
            await self.notify("info", f"{self.link.name} is busy; I'll send that when it finishes"
                              if self.pending or self.session["busy"] else "I'll send that next")
        await self.send_queued()

    async def send_queued(self) -> None:
        """While the agent and the orb are free and a window is open, sends the oldest request, speech queued in a row
        going as one. From the check that the way is clear to the request counting as under way nothing yields, so
        however many callers run this at once, requests go one at a time, in the order they were queued."""
        while self.ready():
            if not self.attached:
                if not await asyncio.to_thread(self.link.probe):
                    await self.drop_queue(self.link.describe(False), "error")
                    return
                self.attached = True
                continue
            request, taken = self.queue[0], 1
            if request.spoken:
                while taken < len(self.queue) and self.queue[taken].spoken:
                    request, taken = request.joined(self.queue[taken]), taken + 1
            del self.queue[:taken]
            await self.dispatch(request)

    async def dispatch(self, request: "Request") -> None:
        text, speak, ref, woken, sender = request.text, request.speak, request.ref, request.woken, request.sender
        request_id = secrets.token_hex(6)
        log.info("request %s sent%s: %d chars", request_id, " (wake word)" if woken else "", len(text))
        self.transcript.append({"req": request_id, "request": text, "reply": None, "status": "pending", "speak": speak, "ref": ref, "woken": woken})
        self.pending[request_id] = {"text": text}
        log.info("state %s -> thinking", self.state)
        self.state = "thinking"
        if request.spoken:
            await self.broadcast({"type": "heard", "text": text})
            if request_id not in self.pending:
                return
        await self.set_state("thinking")
        self.spawn(self.watch_wait(request_id))
        try:
            # Claude Code's link returns None (the reply arrives through the Stop hook); Copilot's blocks and
            # returns the reply itself.
            self.pending[request_id]["sent"] = True
            reply = await asyncio.to_thread(self.link.send, f"{sender} {text}" if sender else text, request_id)
        except agents.RequestEnded as e:
            if request_id in self.pending:
                self.pending.pop(request_id)
                self.entry(request_id)["status"] = "interrupted"
                await self.broadcast({"type": "interrupted", "req": request_id, "ref": ref})
                await self.notify("info", str(e), ref)
                await self.set_state("idle")
            return
        except agents.LinkError as e:
            if request_id in self.pending:
                self.pending.pop(request_id)
                self.entry(request_id)["status"] = "failed"
                self.attached = False
                await self.notify("error", str(e), ref)
                await self.set_state("idle")
            return
        if reply is not None and request_id in self.pending:
            self.pending.pop(request_id)
            await self.deliver_reply(request_id, reply)

    # ---- talking ----

    @property
    def waiting_for_agent(self) -> bool:
        return self.state == "thinking" and bool(self.pending)

    def resume_state(self) -> str:
        return "thinking" if self.pending else "idle"

    @property
    def wake_recording(self) -> bool:
        return self.waker is not None and self.waker.recording is not None

    async def ptt_start(self) -> None:
        """The button takes precedence over the wake word: a wake recording in progress is dropped for it."""
        if (self.busy and not self.waiting_for_agent and not self.wake_recording) or not self.stt:
            return
        if self.waker:
            self.waker.cancel()
            self.waker.follow_up.close()
        self.recording = []
        await self.set_state("listening")

    async def audio_frame(self, data: bytes) -> None:
        frame = np.frombuffer(data, dtype="<f4")
        if self.recording is not None:
            self.recording.append(frame)
        elif self.waker is not None:
            await self.wake_frame(frame)

    async def ptt_stop(self) -> None:
        if self.recording is None:
            return
        samples = np.concatenate(self.recording) if self.recording else np.zeros(0, dtype=np.float32)
        self.recording = None
        log.info("push-to-talk released: %.1fs, loudest %.3f", samples.size / SAMPLE_RATE, float(np.abs(samples).max(initial=0.0)))
        if samples.size < TAP_S * SAMPLE_RATE:
            await self.notify("info", "heard nothing; hold the button while you talk")
            await self.set_state(self.resume_state())
            return
        if self.enrolling is not None:
            await self.enroll_clip(samples)
            return
        await self.heard_clip(samples)

    # ---- the wake word ----

    def wake_snapshot(self) -> dict | None:
        if self.waker is not None:
            return self.waker.describe()
        return {"reason": self.wake_off} if self.wake_off else None

    async def wake_frame(self, frame: np.ndarray) -> None:
        """A frame while nobody holds the button: scored for the wake word while the orb is idle, recorded after
        it fires; heard but never acted on in every other state, so the orb's own speech cannot wake it."""
        async with self.wake_lock:
            if self.waker is None:
                return
            try:
                await self.wake_frame_locked(frame)
            except EngineError as e:
                await self.wake_failed(e)

    async def wake_failed(self, error: Exception) -> None:
        """A wake engine that stops answering (the machine slept, the process died) is started again; a second
        failure within WAKE_RETRY_S turns the wake word off for the session with the reason. The button keeps working."""
        old, self.waker = self.waker, None
        await asyncio.to_thread(old.close)
        if old.recording is not None and self.recording is None and self.state in ("wake_word", "listening"):
            await self.set_state(self.resume_state())
        now = time.monotonic()
        if self.wake_restarted_at is None or now - self.wake_restarted_at > WAKE_RETRY_S:
            self.wake_restarted_at = now
            try:
                self.waker = await asyncio.to_thread(waking.build, self.config["wake"], self.home)
            except waking.WakeOff as e:
                error = e
            if self.waker is not None:
                log.warning("wake engine restarted after: %s", error)
                return
        self.wake_off = str(error)
        log.warning("wake word off: %s", error)
        await self.broadcast({"type": "wake", "wake": self.wake_snapshot()})

    async def wake_frame_locked(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        waker = self.waker
        if waker.recording is None:
            if self.state != "idle" or self.enrolling is not None:
                await asyncio.to_thread(waker.busy_frame, frame)
                self.log_wake_health(now)
                return
            heard = await asyncio.to_thread(waker.idle_frame, frame, now)
            self.log_wake_health(now)
            if heard is None:
                return
            log.info("wake: %s", "fired" if heard == "fired" else "speech in the follow-up window")
            if heard == "fired":
                await self.set_state("wake_word")
            else:
                await self.set_state("listening")
            return
        if self.state == "wake_word":
            await self.set_state("listening")
        if self.state != "listening":
            waker.cancel()
            return
        outcome = await asyncio.to_thread(waker.recording_frame, frame, now)
        if outcome is None:
            return
        samples = waker.take()
        log.info("wake recording %s: %.1fs", "ended" if outcome == "ended" else "heard no speech", samples.size / SAMPLE_RATE)
        if outcome == "nothing":
            await self.set_state(self.resume_state())
            return
        self.spawn(self.heard_wake_clip(samples))

    def log_wake_health(self, now: float) -> None:
        report = self.waker.health.report(now)
        if report is None:
            return
        line = (f"wake health: {report['frames']}/{report['expected']} frames in {report['seconds']}s, engine "
                f"{report['engine_ms']} ms/frame, top score {report['top']}, loudest {report['loudest']}")
        if report["starved"]:
            log.warning("%s — starved: the phrase arrives in pieces (machine overloaded or window throttled)", line)
        else:
            log.info("%s", line)

    async def heard_wake_clip(self, samples: np.ndarray) -> None:
        ok, similarity = await asyncio.to_thread(self.waker.authorized, samples)
        if similarity is not None:
            log.info("voice auth: similarity %.2f, %s", similarity, "accepted" if ok else "refused")
        if not ok:
            self.transcript.append({"kind": "refused", "text": f"a voice that is not the enrolled one (similarity {similarity:.2f})"})
            await self.notify("info", "that wasn't the enrolled voice")
            await self.set_state(self.resume_state())
            return
        await self.heard_clip(samples, strip=self.waker.strip)

    async def heard_clip(self, samples: np.ndarray, strip=None) -> None:
        await self.set_state("transcribing")
        text = await asyncio.to_thread(self.stt.transcribe, samples)
        log.info("heard %.1fs: %r", samples.size / SAMPLE_RATE, text[:200])
        if strip is not None:
            text = strip(text)
        if self.state == "transcribing":
            self.state = self.resume_state()
        if not text.strip():
            await self.notify("info", "didn't catch that")
            if self.state != "speaking":
                await self.set_state(self.resume_state())
            return
        intent = side_intent(text)
        if intent is not None and (self.pending or self.session["busy"] or intent == "missed"):
            await self.side_question(text, intent)
            return
        waits = self.must_wait()
        await self.submit(Request(text, woken=strip is not None, spoken=True))
        if waits and self.state in ("idle", "thinking"):
            await self.set_state(self.state)
        elif not waits and self.state == "idle":
            await self.set_state("idle")

    async def side_question(self, text: str, intent: str) -> None:
        """Speech while the agent is busy that the orb answers itself: its last reply, what is running, what was
        missed."""
        if intent == "missed":
            answer = await self.missed()
        else:
            answer = self.last_reply() if intent == "repeat" else await self.status()
        await self.broadcast({"type": "side", "heard": text, "answer": answer})
        if self.tts and self.voice:
            self.speaking = asyncio.create_task(self.speak(answer))
        else:
            await self.set_state(self.resume_state())

    async def drop_queue(self, why: str, level: str = "info", upto: int | None = None) -> None:
        """Nothing queued outlives the turn it waited on being stopped, the session it was meant for, or an agent
        that is not there to send it to. `upto` keeps what was queued after the first that many, made after Stop."""
        queued, self.queue = self.queue[:upto], self.queue[len(self.queue) if upto is None else upto:]
        if not queued:
            return
        for request in queued:
            if request.ref is not None:
                await self.notify(level, f"not sent: {why}", request.ref)
        await self.notify(level, f"dropped what was waiting to be sent: {why}")

    async def status(self) -> str:
        doing = await asyncio.to_thread(self.link.doing) or self.waiting
        return status_sentence(self.link.name, doing, [t for t in self.background if t["description"] != doing])

    async def missed(self) -> str:
        """The replies that finished since the window was last looked at, read out; marks them seen."""
        replies = unread(self.session["feed"], self.seen_id)
        self.heard.update(r["id"] for r in replies)
        await self.mark_seen()
        if not replies:
            return "Nothing new since you last looked."
        lead = "One reply" if len(replies) == 1 else f"{len(replies)} replies"
        return f"{lead} came in while you were away. " + " ".join(r["text"] for r in replies[-3:])

    def last_reply(self) -> str:
        return self.last_said or "There is nothing to repeat yet."

    # ---- speaking ----

    @property
    def speed(self) -> float:
        return float(getattr(self.tts, "speed", speed_for(self.config["tts"], self.agent)))

    async def set_speed(self, value: float) -> None:
        """Takes effect on the next sentence rendered; kept per agent in the user's config for this repo."""
        speed = clamp_speed(value)
        if self.tts is not None:
            self.tts.speed = speed
        await asyncio.to_thread(save_speed, self.home, self.repo_root, self.agent, speed)
        await self.broadcast({"type": "speed", "value": speed})

    async def set_voice(self, on: bool) -> None:
        """Muting stops speech at once; while muted nothing is rendered, so a cloud engine is not called. Kept in
        the user's config for this repo."""
        log.info("voice %s", "on" if on else "muted")
        self.voice = on
        if not on and self.state == "speaking":
            await self.stop()

        def update(data: dict) -> None:
            data["speak"] = on

        await asyncio.to_thread(save_for_repo, self.home, self.repo_root, update)
        await self.broadcast({"type": "voice", "on": on})

    def enroll_snapshot(self) -> dict | None:
        if self.enrolling is None:
            return None
        done = len(self.enrolling["embeddings"])
        return {"phrase": ENROLL_PHRASES[done], "index": done, "total": len(ENROLL_PHRASES)}

    async def enroll_endpoint(self, request: web.Request) -> web.Response:
        """Starts enrolling the user's voiceprint: the window shows a phrase, the user holds the button and reads it,
        five times; the mean embedding is saved for the voiceprint engine the config names."""
        engine = (self.config["wake"] or {}).get("voiceprint", "resemblyzer")
        if self.enroll_engine is None:
            try:
                self.enroll_engine = await asyncio.to_thread(Voiceprint, lambda name: {"engine": name or engine}, self.home)
            except EngineError as e:
                raise web.HTTPBadRequest(text=str(e))
        if self.enroll_engine.name != engine:
            reason, fallen = self.enroll_engine.fallback_reason, self.enroll_engine
            self.enroll_engine = None
            await asyncio.to_thread(fallen.close)
            raise web.HTTPBadRequest(text=f"the {engine} voiceprint engine did not load, so nothing was enrolled: {reason}")
        self.enrolling = {"engine": engine, "embeddings": []}
        await self.broadcast({"type": "enroll", **self.enroll_snapshot()})
        return web.json_response({"phrases": len(ENROLL_PHRASES)})

    async def stop_enrolling(self, reason: str) -> None:
        """Ends an enrollment that failed; the engine is closed and dropped, so the next one starts from a fresh load."""
        self.enrolling, engine, self.enroll_engine = None, self.enroll_engine, None
        await asyncio.to_thread(engine.close)
        await self.notify("error", f"enrollment stopped: {reason}")
        await self.set_state(self.resume_state())

    async def enroll_clip(self, samples: np.ndarray) -> None:
        await self.set_state("transcribing")
        try:
            vector = await asyncio.to_thread(self.enroll_engine.embed, samples)
        except EngineError as e:
            await self.stop_enrolling(str(e))
            return
        if self.enroll_engine.failure or self.enroll_engine.name != self.enrolling["engine"]:
            await self.stop_enrolling(f"the voiceprint engine failed ({self.enroll_engine.failure or self.enroll_engine.fallback_reason}); nothing was saved")
            return
        self.enrolling["embeddings"].append(vector.tolist())
        if len(self.enrolling["embeddings"]) < len(ENROLL_PHRASES):
            await self.broadcast({"type": "enroll", **self.enroll_snapshot()})
        else:
            path = await asyncio.to_thread(waking.save_voiceprint, self.home, self.enrolling["engine"],
                                           voiceprint_from(self.enrolling["embeddings"]))
            self.enrolling = None
            await self.broadcast({"type": "enroll", "done": True})
            await self.notify("info", f"voiceprint enrolled ({path}); reopen the orb for voice auth to use it")
        await self.set_state(self.resume_state())

    async def voice_endpoint(self, request: web.Request) -> web.Response:
        body = await request.json()
        on = body.get("on")
        await self.set_voice(not self.voice if on is None else bool(on))
        return web.json_response({"on": self.voice})

    async def speak(self, text: str, follow_up: bool = False, said: str | None = None) -> None:
        """`follow_up` opens the wake word's follow-up window after the reply: only for an answer to a request made
        by the wake word, never after the button (the user holds it again) or a turn typed in the terminal."""
        if not self.voice:
            await self.set_state(self.resume_state())
            return
        sentences = split_sentences(speakable(text))
        self.played.clear()
        await self.set_state("speaking", said)
        seconds, started, first = 0.0, time.monotonic(), None
        try:
            await self.broadcast({"type": "speak_start", "sentences": len(sentences)})
            for sentence in sentences:
                samples, rate = await asyncio.to_thread(self.tts.render, sentence)
                await self.report_tts_failure()
                first = first if first is not None else time.monotonic() - started
                seconds += samples.size / rate
                await self.broadcast_bytes(audio_frame(samples, rate))
            await self.broadcast({"type": "speak_end"})
            log.info("speaking %d sentences with %s: %.1fs of audio, first after %.2fs, all rendered in %.2fs",
                     len(sentences), self.tts.name, seconds, first or 0.0, time.monotonic() - started)
            try:
                await asyncio.wait_for(self.played.wait(), seconds + PLAYBACK_GRACE_S)
            except asyncio.TimeoutError:
                log.warning("page did not report playback done within %.0fs", seconds + PLAYBACK_GRACE_S)
        except asyncio.CancelledError:
            log.info("speech stopped")
            await self.broadcast({"type": "speak_stop"})
            raise
        finally:
            if self.state == "speaking":
                if follow_up and self.waker is not None and self.resume_state() == "idle":
                    self.waker.follow_up.open(time.monotonic())
                    self.spawn(self.send_queued_after(self.waker.follow_up.seconds))
                await self.set_state(self.resume_state())

    async def report_tts_failure(self) -> None:
        failure = self.tts.failure
        if failure and failure != self.reported_tts_failure:
            log.warning("speech: %s", failure)
            await self.notify("error", f"speech: {failure}")
        self.reported_tts_failure = failure

    async def stop(self) -> None:
        """Speech first, whatever the state says: a reply still playing is what the user hears and wants stopped. The
        session replies queued behind it are stopped too, so the next one does not start the moment this one ends."""
        log.info("stop pressed while %s", self.state)
        if self.speaking and not self.speaking.done():
            await self.stop_speaking()
            self.heard.update(item["id"] for item in self.session.get("feed", ()) if undelivered_final(item))
            if self.state == "speaking":
                await self.set_state(self.resume_state())
        elif self.state in ("listening", "wake_word"):
            self.recording = None
            if self.waker is not None:
                self.waker.cancel()
            await self.set_state(self.resume_state())
        elif self.interrupted_at is not None and time.monotonic() - self.interrupted_at < INTERRUPT_SETTLE_S:
            # A second Escape into a session that has just stopped would open its rewind menu.
            log.info("stop ignored: an interrupt went out %.1fs ago", time.monotonic() - self.interrupted_at)
        elif self.state == "thinking":
            self.interrupted_at = time.monotonic()
            await self.interrupt()
        elif self.session.get("busy"):
            self.interrupted_at = time.monotonic()
            await self.interrupt_session()

    async def interrupt_session(self) -> None:
        """Stop while the agent works on a turn the orb did not send — one typed in the terminal, or one the orb
        already stopped waiting for: the same abort, with nothing of the orb's to mark."""
        waiting = len(self.queue)
        stopped = await asyncio.to_thread(self.link.interrupt)
        log.info("interrupt of the session's own turn: %s", stopped.value)
        if stopped is agents.Stopped.ABORTED:
            await self.notify("info", f"stopped {self.link.name}")
            await self.drop_queue("Stop ended the turn it was waiting on", upto=waiting)
        else:
            await self.notify("info", f"{self.link.name} could not be stopped from here; it keeps going in its own window")

    async def interrupt(self) -> None:
        """Stop while thinking: abort the agent's turn where an adapter can, and mark the request interrupted at
        once, since no reply will come for it (Claude Code's Stop hook does not fire on an interrupted turn)."""
        if not self.pending:
            await self.set_state("idle")
            return
        request_id = next(iter(self.pending))
        # Dropped from pending before the abort goes out: an aborted sendAndWait returns at once with an empty
        # reply, and that must not be delivered as an answer.
        held = self.pending.pop(request_id)
        self.entry(request_id)["status"] = "interrupted"
        waiting = len(self.queue)
        stopped = await asyncio.to_thread(self.link.interrupt)
        log.info("interrupt of request %s: %s", request_id, stopped.value)
        if stopped is agents.Stopped.STILL_WAITING and held.get("sent"):
            self.pending[request_id] = held
            self.entry(request_id)["status"] = "pending"
            await self.notify("info", "the agent is still working; Stop ends speech only")
            return
        await self.broadcast({"type": "interrupted", "req": request_id, "ref": self.entry(request_id).get("ref")})
        if stopped is agents.Stopped.RELEASED:
            await self.notify("info", "nothing was aborted; the agent keeps going in its own window")
        else:
            await self.drop_queue("Stop ended the turn it was waiting on", upto=waiting)
        await self.set_state("idle")

    # ---- socket ----

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        self.sockets.add(ws)
        log.info("window connected (%d open)", len(self.sockets))
        self.empty_since = None
        await ws.send_json(self.snapshot())
        self.spawn(self.send_queued())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self.on_message(json.loads(msg.data))
                elif msg.type == WSMsgType.BINARY:
                    await self.audio_frame(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.sockets.discard(ws)
            log.info("window closed (%d open)", len(self.sockets))
            if not self.sockets:
                self.empty_since = time.monotonic()
        return ws

    async def on_message(self, message: dict) -> None:
        # A request runs as its own task: Copilot's link blocks until the reply, and the socket loop has to keep
        # reading so that Stop can arrive meanwhile.
        kind = message.get("type")
        if kind == "ask":
            if message.get("active") is not True:
                await self.notify("error", "orb.send works only during a click or key press", message.get("ref"))
                return
            self.spawn(self.ask(str(message.get("text", "")), speak=bool(message.get("speak", True)), ref=message.get("ref"),
                                sender=page_sender(message)))
        elif kind == "stop":
            await self.stop()
        elif kind == "seen":
            await self.mark_seen()
        elif kind == "ptt_start":
            await self.ptt_start()
        elif kind == "ptt_stop":
            self.spawn(self.ptt_stop())
        elif kind == "played":
            self.played.set()
        elif kind == "speed":
            try:
                await self.set_speed(float(message.get("value")))
            except (TypeError, ValueError):
                pass
        elif kind == "skin_size":
            try:
                await self.set_skin_size(float(message.get("value")))
            except (TypeError, ValueError):
                pass
        elif kind == "voice":
            await self.set_voice(bool(message.get("on")))
        elif kind == "speak_text":
            if message.get("active") is not True:
                log.info("a request to read aloud arrived without a click or key press; ignored")
                return
            await self.speak_text(str(message.get("text", "")), message.get("said"))
        elif kind == "log":
            log.warning("page: %s", str(message.get("text", ""))[:500])
        elif kind == "tts_voice":
            await self.set_tts_voice(str(message.get("id", "")))
        elif kind == "tts_engine":
            await self.set_tts_engine(str(message.get("name", "")))
        elif kind == "stt_engine":
            await self.set_stt_engine(str(message.get("name", "")))
        elif kind == "action":
            self.spawn(self.page_action(message))
        elif kind == "theme":
            await self.set_theme(str(message.get("name", "")))
        elif kind == "skin":
            await self.set_skin(str(message.get("name", "")))
        elif kind == "skin_option":
            await self.set_skin_option(str(message.get("option", "")), str(message.get("value", "")))
        elif kind == "panel_close":
            name = str(message.get("name", ""))
            if name in self.panels:
                self.drop_panel(name)
                await self.broadcast({"type": "panel_close", "name": name})
        elif kind == "widget":
            name = str(message.get("name", ""))
            problem = await (self.widget_on(name) if message.get("on") else self.widget_off(name))
            if problem:
                await self.notify("error", f"widget {name}: {problem}")
        elif kind == "widget_place":
            await self.place_widget(str(message.get("name", "")), message)


    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(message)
            except ConnectionResetError:
                self.sockets.discard(ws)

    async def broadcast_bytes(self, data: bytes) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_bytes(data)
            except ConnectionResetError:
                self.sockets.discard(ws)

    async def set_state(self, state: str, said: str | None = None) -> None:
        assert state in SERVER_STATES, state
        if state != self.state:
            log.info("state %s -> %s", self.state, state)
        self.state, self.said = state, said
        await self.broadcast({"type": "state", "state": state, **({"said": said} if said else {})})
        if state == "idle" and self.queue:
            self.spawn(self.send_queued())

    async def notify(self, level: str, text: str, ref=None) -> None:
        (log.warning if level == "error" else log.info)("notice (%s): %s", level, text)
        await self.broadcast({"type": "notice", "level": level, "text": text, "ref": ref})

    # ---- lifecycle ----

    async def watch_attachment(self) -> None:
        while True:
            was = self.attached
            self.attached = await asyncio.to_thread(self.link.probe)
            if was and not self.attached:
                await self.drop_queue(f"{self.link.name} went away")
            await self.announce_attachment()
            await self.announce_tasks()
            await asyncio.sleep(ATTACH_RECHECK_S)

    async def stop_server(self, request: web.Request) -> web.Response:
        """`orb stop`: tells every window the orb is closing, then lets run() shut the server down."""
        await self.broadcast({"type": "closing"})
        self.stopping = True
        return web.json_response({"ok": True})

    async def watch_window(self, grace: float = WINDOW_GRACE_S, poll: float = 1) -> None:
        """Returns once no page has been connected for `grace` seconds, or at once after a stop."""
        while not self.stopping and (self.sockets or self.empty_since is None or time.monotonic() - self.empty_since < grace):
            await asyncio.sleep(poll)

    async def run(self) -> None:
        # The first answer to /health already says whether an agent is attached, so `orb open` never reports "not
        # attached" for the moment before the first check has run.
        self.attached = await asyncio.to_thread(self.link.probe)
        self.voice_menu = await asyncio.to_thread(self.tts.voices) if self.tts else None
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", self.port).start()
        write_json(port_file(state_dir(self.home, self.repo_root), self.agent), {"port": self.port})
        write_json(server_file(state_dir(self.home, self.repo_root), self.agent),
                   {"port": self.port, "token": self.token, "pid": os.getpid(), "agent": self.agent,
                    "repo": str(self.repo_root)})
        log.info("listening on %s for %s (%s)", self.origin, self.repo_root, self.agent)
        await self.show_saved_widgets()
        attachment = asyncio.create_task(self.watch_attachment())
        session = asyncio.create_task(self.watch_session())
        panel_files = asyncio.create_task(self.watch_pages())
        await self.watch_window()
        log.info("stopped" if self.stopping else "no window for %ds; exiting", *(() if self.stopping else (WINDOW_GRACE_S,)))
        attachment.cancel()
        session.cancel()
        panel_files.cancel()
        for open_pages in self.open_pages().values():
            for name in list(open_pages):
                self.drop_page(open_pages, name)
        mine = server_file(state_dir(self.home, self.repo_root), self.agent)
        if (read_json(mine) or {}).get("pid") == os.getpid():
            mine.unlink(missing_ok=True)
        for ws in list(self.sockets):
            await ws.close()
        await runner.cleanup()


def make_link(agent: str, state: Path, config):
    if agent == "claude":
        return ClaudeLink(state)
    from orb.copilot import CopilotLink
    return CopilotLink(copilot_port_for(config, os.environ), turns=state)


def page_sender(message: dict) -> str:
    """The mark a page's request carries to the agent: which panel or widget sent it."""
    kind = message.get("kind") if message.get("kind") in ("panel", "widget") else "page"
    name = message.get("page") if isinstance(message.get("page"), str) and pages.NAME.match(message["page"]) else "unknown"
    return f'[sent by the orb {kind} "{name}", not spoken]'


def load_engines(config, home: Path, agent: str):
    log.info("loading speech engines: stt=%s tts=%s", config["stt"]["engine"], config["tts"]["engine"])
    stt = Stt(lambda name: engine_settings(config["stt"] | ({"engine": name} if name else {})), home)
    tts = Tts(lambda name: tts_settings_for(config["tts"] | ({"engine": name} if name else {}), agent), home)
    for engine in (stt, tts):
        if engine.fallback_reason:
            log.warning("%s", engine.fallback_reason)
    log.info("engines ready")
    return stt, tts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--agent", required=True, choices=("claude", "copilot"))
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--home", type=Path, default=Path.home())
    return parser


def read_token(stream) -> str:
    token = stream.readline().strip()
    if not token:
        raise SystemExit("orb server: no token on standard input")
    return token


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    token = read_token(sys.stdin)
    repo = args.repo.resolve()
    logs.setup(logs.log_path(state_dir(args.home, repo), args.agent))
    config = load_for(repo, args.home)
    for warning in config.warnings:
        log.warning(warning)
    try:
        stt, tts = load_engines(config, args.home, args.agent)
    except EngineError as e:
        log.error("%s", e)
        raise SystemExit(2)
    waker, wake_off = None, None
    try:
        waker = waking.build(config["wake"], args.home)
    except waking.WakeOff as e:
        wake_off = str(e)
        log.warning("wake word off: %s", e)
    link = make_link(args.agent, state_dir(args.home, repo), config)
    server = OrbServer(repo, args.agent, args.port, token, args.home, link, stt, tts, waker, wake_off)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
