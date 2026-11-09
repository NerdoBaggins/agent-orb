import asyncio
import threading

from orb import server as server_module
from orb.test_server import FakeStt, InterruptibleLink, client_for, make, run, wait_for
from orb.test_server_side import CLIP, connect


class HeldStt(FakeStt):
    """Hears each of `texts` in turn, the last one from then on."""
    def __init__(self, *texts):
        super().__init__(texts[-1])
        self.texts = list(texts)
        self.release, self.started = threading.Event(), threading.Event()
        self.transcribing, self.closed, self.closed_while_transcribing = False, False, False

    def transcribe(self, samples) -> str:
        self.transcribing = True
        self.started.set()
        self.release.wait(3)
        self.transcribing = False
        super().transcribe(samples)
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def close(self):
        self.closed, self.closed_while_transcribing = True, self.transcribing


async def stopped_mid_transcription(server):
    heard = asyncio.create_task(server.heard_clip(CLIP))
    await asyncio.to_thread(server.stt.started.wait, 3)
    await server.stop()
    return heard


def held_server(tmp_path, *texts):
    server = make(tmp_path, voice=True)
    server.stt = HeldStt(*(texts or ("delete everything",)))
    return server


def test_stop_while_transcribing_returns_the_orb_to_idle_before_the_transcription_ends(tmp_path):
    async def go():
        server = held_server(tmp_path)
        heard = await stopped_mid_transcription(server)
        assert server.state == "idle" and server.stt.transcribing
        server.stt.release.set()
        await heard
    run(go())


def test_words_heard_before_stop_never_reach_the_agent(tmp_path):
    async def go():
        server = held_server(tmp_path)
        async with await client_for(server) as c:
            await connect(c)
            heard = await stopped_mid_transcription(server)
            server.stt.release.set()
            await heard
            await asyncio.sleep(0.2)
            assert server.link.sent == [] and server.queue == [] and server.state == "idle"
    run(go())


def test_a_clip_stopped_mid_transcription_is_not_reported_as_unheard(tmp_path):
    async def go():
        server = held_server(tmp_path, "")
        notices = []
        server.notify = lambda level, text, ref=None: asyncio.sleep(0, notices.append(text))
        heard = await stopped_mid_transcription(server)
        server.stt.release.set()
        await heard
        assert notices == []
    run(go())


def test_a_clip_spoken_after_stop_still_reaches_the_agent(tmp_path):
    async def go():
        server = held_server(tmp_path, "delete everything", "what time is it")
        async with await client_for(server) as c:
            await connect(c)
            stopped = await stopped_mid_transcription(server)
            server.stt.started.clear()
            spoken_after = asyncio.create_task(server.heard_clip(CLIP))
            server.stt.release.set()
            await stopped
            await spoken_after
            await wait_for(lambda: server.link.sent)
            assert [sent[0] for sent in server.link.sent] == ["what time is it"]
    run(go())


def test_stop_while_transcribing_leaves_a_busy_agent_working(tmp_path):
    async def go():
        server = held_server(tmp_path)
        server.link = InterruptibleLink()
        server.session = server.session | {"busy": True}
        heard = await stopped_mid_transcription(server)
        server.stt.release.set()
        await heard
        assert server.link.interrupts == 0
    run(go())


def test_switching_engines_waits_for_a_transcription_stop_left_running(tmp_path, monkeypatch):
    async def go():
        server = held_server(tmp_path)
        old, new = server.stt, FakeStt("")
        new.name = "other"
        monkeypatch.setattr(server_module, "Stt", lambda settings_for, home: new)
        heard = await stopped_mid_transcription(server)
        switched = asyncio.create_task(server.use_stt(server.config["stt"] | {"engine": "other"}))
        await asyncio.sleep(0.2)
        old.release.set()
        await heard
        assert await switched is None
        assert server.stt is new and old.closed and not old.closed_while_transcribing
    run(go())
