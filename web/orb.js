// The orb page. One WebSocket to the orb server; the token comes from the launched URL and is appended to every
// request. Push-to-talk streams 16 kHz float32 frames up; rendered sentences come down as binary frames
// (uint32 sample rate + float32 PCM) and play through Web Audio. Panels are sandboxed iframes that reach the
// session only through postMessage to this page.
(() => {
  const token = document.body.dataset.token;
  const $ = (id) => document.getElementById(id);
  const orb = {
    state: "idle",
    level: 0,
    attached: false,
    voice: false,
    waiting: null,
    pttKey: "Space",
    muteKey: "KeyM",
    panelsKey: "KeyP",
    speak: true,
    wake: null,
    enrolling: null,
    ws: null,
    transcript: [],
    session: { busy: false, doing: null, feed: [], unread: 0 },
    attachText: "",
    panels: new Map(),
    widgets: new Map(),
    activePanel: null,
    size: NORMAL_SIZE,
  };

  // ---- header and notices ----

  const baseTitle = document.title;

  // ---- background tasks ----

  const backgroundUi = {
    tasks: [],
    timer: null,
    set(tasks) {
      this.tasks = tasks || [];
      const pill = $("bar-tasks");
      pill.hidden = this.tasks.length === 0;
      pill.textContent = `${this.tasks.length} running`;
      if (!this.tasks.length) $("tasks-list").hidden = true;
      this.render();
    },
    age(startedAt) {
      const s = Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000));
      return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m`;
    },
    render() {
      const list = $("tasks-list");
      list.replaceChildren(...this.tasks.map((t) => {
        const row = document.createElement("div");
        row.className = "task-row";
        for (const [cls, text] of [["task-desc", t.description], ["task-kind", t.kind], ["task-age", t.startedAt ? this.age(t.startedAt) : ""]]) {
          const cell = document.createElement("span");
          cell.className = cls;
          cell.textContent = text;
          row.appendChild(cell);
        }
        row.title = t.description;
        return row;
      }));
    },
    toggle() {
      const list = $("tasks-list");
      list.hidden = !list.hidden;
      clearInterval(this.timer);
      if (!list.hidden) this.timer = setInterval(() => this.render(), 1000);
    },
  };
  const BAR_LINGER_MS = 1000;
  const bar = document.querySelector(".bar");
  let barClosing = null;
  bar.addEventListener("pointerenter", () => { clearTimeout(barClosing); bar.classList.add("open"); });
  bar.addEventListener("pointerleave", () => { barClosing = setTimeout(() => bar.classList.remove("open"), BAR_LINGER_MS); });
  $("bar-tasks").addEventListener("click", (ev) => { ev.stopPropagation(); backgroundUi.toggle(); });
  addEventListener("click", (ev) => {
    if (!ev.target.closest(".bar-tasks-wrap")) $("tasks-list").hidden = true;
    if (!ev.target.closest(".bar-widgets-wrap")) $("widgets-list").hidden = true;
  });

  function showSpeed(value) {
    $("speed").value = value;
    $("speed-value").textContent = `${Number(value).toFixed(2)}×`;
  }

  let speedTimer = null;
  $("speed").addEventListener("input", (ev) => {
    const value = Number(ev.target.value);
    $("speed-value").textContent = `${value.toFixed(2)}×`;
    clearTimeout(speedTimer);
    speedTimer = setTimeout(() => send({ type: "speed", value }), 150);
  });

  let sizeDragging = false;
  $("size").addEventListener("pointerdown", () => { sizeDragging = true; });
  addEventListener("pointerup", (ev) => {
    sizeDragging = false;
    const r = bar.getBoundingClientRect();
    const over = ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    if (!over && bar.contains(document.activeElement)) document.activeElement.blur();
  });
  function showSize(value) {
    orb.size = value;
    $("size").value = value;
    $("size-value").textContent = sizeLabel(value);
  }
  let sizeTimer = null;
  $("size").addEventListener("input", (ev) => {
    const value = Number(ev.target.value);
    showSize(sizeDragging ? snapSize(value) : value);
    clearTimeout(sizeTimer);
    sizeTimer = setTimeout(() => send({ type: "skin_size", value: orb.size }), 150);
  });

  function setAttached(attached, text) {
    orb.attached = attached;
    const el = $("attach");
    el.textContent = text;
    el.className = "bar-link " + (attached ? "ok" : "bad");
    orb.attachText = attached ? text.replace(/^Attached: /, "") : "";
    orb.attachMessage = text;
    refreshTitle();
    updateMic();
  }

  function refreshTitle() {
    const unread = orb.session.unread ? `(${orb.session.unread}) ` : "";
    document.title = unread + (orb.attachText ? `${orb.attachText} · Orb` : baseTitle);
    const chip = $("bar-unread");
    chip.hidden = !orb.session.unread;
    chip.textContent = `${orb.session.unread} new`;
  }

  // What the session is doing whoever started it: a turn typed in the terminal looks like one the orb sent.
  function shownState() {
    return orb.state === "idle" && orb.session.busy ? "thinking" : orb.state;
  }

  const sessionUi = {
    apply(session) {
      orb.session = session;
      refreshTitle();
      updateMic();
      transcriptUi.render();
      this.markSeen();
    },
    markSeen() {
      if (orb.session.unread && document.hasFocus() && !document.hidden) send({ type: "seen" });
    },
  };
  addEventListener("focus", () => sessionUi.markSeen());
  document.addEventListener("visibilitychange", () => sessionUi.markSeen());
  $("bar-unread").addEventListener("click", () => send({ type: "seen" }));

  const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>';
  const STOP_ICON = '<svg viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" rx="2" fill="currentColor"/></svg>';
  const STATE_WORDS = { listening: "listening", transcribing: "transcribing…", thinking: "thinking…", speaking: "speaking" };
  function updateMic() {
    const mic = $("mic");
    const busy = orb.state !== "idle";
    const working = orb.state === "thinking";
    const stopping = busy && !working && orb.state !== "listening";
    mic.disabled = working ? !orb.voice : !busy && !((orb.attached || orb.enrolling) && orb.voice);
    const label = working ? "hold to ask" : busy ? (orb.state === "listening" ? "release to send" : "stop") : "hold to talk";
    mic.innerHTML = stopping ? STOP_ICON : MIC_ICON;
    mic.title = label;
    mic.setAttribute("aria-label", label);
    mic.classList.toggle("stop", stopping);
    mic.classList.toggle("active", busy);
    const outside = !busy && orb.session.busy;
    // The caption names what the orb is doing in words, since a colour alone does not say it.
    $("caption").textContent = orb.stopping ? "stopping…" : (STATE_WORDS[orb.state] || (outside ? "working…" : ""));
    $("stop").hidden = !working && !outside;
    if (orb.stopping && !working && !outside) orb.stopping = false;
    const how = orb.enrolling ? `enrolling ${orb.enrolling.index + 1} of ${orb.enrolling.total}: hold and read — “${orb.enrolling.phrase}”`
      : orb.voice ? `or hold ${orb.pttKey} while this window has focus${orb.wake ? `, or say “${orb.wake.phrase}”` : ""}` : "speech engines not loaded";
    const doing = (working || outside) && orb.session.doing;
    $("hint").textContent = working && orb.waiting ? `waiting for ${orb.waiting} to finish`
      : doing ? `working: ${orb.session.doing}` : outside ? "working…" : how;
    // The how-to shows on hover; enrolment's instructions and what the agent is busy with show without it.
    $("hint").classList.toggle("shown", !!orb.enrolling || (working && !!orb.waiting) || !!doing || outside);
  }

  let noticeTimer;
  // An error the window finds itself (the mic, a skin) also goes to the server's log; one the server sent is there.
  function notice(text, level = "info", fromServer = false) {
    if (level === "error" && !fromServer && text) send({ type: "log", text });
    const el = $("notice");
    el.textContent = text;
    el.className = level === "error" ? "error" : "";
    clearTimeout(noticeTimer);
    if (text) noticeTimer = setTimeout(() => { el.textContent = ""; }, level === "error" ? 12000 : 6000);
  }

  function setState(state, said) {
    orb.state = state;
    document.body.dataset.state = state;
    updateMic();
    panelsBridge.broadcastState(state);
    transcriptUi.saying = state === "speaking" ? said || null : null;
    transcriptUi.syncSpeakers();
    releasesUi.settle();
  }

  // ---- socket ----

  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}`);
    ws.binaryType = "arraybuffer";
    orb.ws = ws;
    ws.onmessage = (ev) => (ev.data instanceof ArrayBuffer ? onAudio(ev.data) : onMessage(JSON.parse(ev.data)));
    ws.onclose = () => {
      if (orb.closing) return;
      setAttached(false, "orb server gone; close this window and run orb open again");
      setTimeout(connect, 2000);
    };
  }

  function onMessage(m) {
    switch (m.type) {
      case "snapshot":
        orb.voice = m.voice;
        orb.pttKey = m.ptt_key || "Space";
        orb.muteKey = m.mute_key || "KeyM";
        orb.panelsKey = m.panels_key || "KeyP";
        showVoice(m.speak !== false);
        wakeUi.apply(m.wake);
        enrollUi.apply(m.enrolling);
        orb.waiting = m.waiting || null;
        showSpeed(m.speed ?? 1);
        backgroundUi.set(m.background);
        showEngines(m.engines, m.tts_engines || [], m.stt_engines || []);
        showVoices(m.voices);
        for (const kind of ["stt", "tts"]) {
          if (m.engines[kind] && m.engines[kind].fallback_reason) notice(`${kind}: ${m.engines[kind].fallback_reason}`, "error");
        }
        orb.transcript = m.transcript || [];
        orb.session = m.session || orb.session;
        setAttached(m.attached, m.attach_text);
        setState(m.state, m.said);
        skinPicker.render(m.skins || [], m.skin.name, m.skin_key);
        themePicker.render(m.themes || [], m.theme);
        skin.load(m.skin);
        showSize(m.skin.size);
        for (const p of m.panels || []) panelsUi.show(p, { restoring: true });
        widgetsUi.snapshot(m.widgets);
        transcriptUi.render();
        break;
      case "widget":
        widgetsUi.show(m);
        break;
      case "widget_close":
        widgetsUi.close(m.name);
        break;
      case "widget_data":
        widgetsUi.data(m.name, m.data, m.at);
        break;
      case "widget_place":
        widgetsUi.placed(m);
        break;
      case "widgets_installed":
        widgetsUi.lists(m);
        break;
      case "pickers":
        skinPicker.render(m.skins, skinPicker.current, skinPicker.key);
        themePicker.render(m.themes, { name: themePicker.current });
        break;
      case "session":
        sessionUi.apply({ busy: m.busy, doing: m.doing, since: m.since, feed: mergeFeed(orb.session.feed, m.feed, m.order), unread: m.unread });
        break;
      case "waiting":
        orb.waiting = m.text || null;
        updateMic();
        break;
      case "side":
        notice(m.answer);
        break;
      case "background":
        backgroundUi.set(m.tasks);
        break;
      case "engines":
        showEngines(m.engines, null, null);
        showVoices(m.voices);
        break;
      case "voices":
        showVoices(m.voices);
        break;
      case "skin":
        if (!skin.info || m.name !== skin.info.name) location.reload();  // a new skin starts on a fresh page
        break;
      case "theme":
        if (m.name !== themePicker.current) location.reload();  // the theme's stylesheets load with the page
        break;
      case "skin_option":
        skinOptions.apply(m.option, m.value);
        break;
      case "speed":
        showSpeed(m.value);
        break;
      case "skin_size":
        if (!sizeDragging) showSize(m.value);
        break;
      case "voice":
        showVoice(m.on);
        break;
      case "wake":
        wakeUi.apply(m.wake);
        break;
      case "enroll":
        enrollUi.apply(m.done ? null : m);
        break;
      case "attached":
        setAttached(m.attached, m.text);
        break;
      case "state":
        setState(m.state, m.said);
        break;
      case "heard":
        orb.transcript.push({ request: m.text, reply: null, status: "pending" });
        transcriptUi.render();
        break;
      case "reply":
        transcriptUi.answer(m.req, m.text, "answered");
        panelsBridge.resolve(m.ref, m.text);
        releasesUi.replied(m.ref);
        break;
      case "interrupted":
        transcriptUi.answer(m.req, null, "interrupted");
        panelsBridge.reject(m.ref, "interrupted");
        releasesUi.ended(m.ref);
        break;
      case "notice":
        notice(m.text, m.level === "error" ? "error" : "info", true);
        if (m.ref) { panelsBridge.reject(m.ref, m.text); releasesUi.ended(m.ref); }
        break;
      case "speak_start":
        player.start();
        break;
      case "speak_end":
        player.end();
        break;
      case "speak_stop":
        player.stop();
        break;
      case "panel":
        panelsUi.show(m);
        break;
      case "action_result":
        panelsBridge.answer(m);
        break;
      case "panel_close":
        panelsUi.close(m.name);
        break;
      case "closing":
        orb.closing = true;
        setAttached(false, "orb stopped");
        notice("orb stopped; this window can be closed");
        break;
      case "panel_data":
        panelsUi.data(m.name, m.data, m.at);
        break;
      case "feed_status":
        (m.kind === "widget" ? widgetsUi : panelsUi).stale(m.name, m.error);
        break;
      case "page_status":
        if (m.kind === "widget") widgetsUi.failed(m.name, m.error);
        break;
    }
  }

  function send(message) {
    if (orb.ws && orb.ws.readyState === WebSocket.OPEN) orb.ws.send(JSON.stringify(message));
  }

  // ---- playback ----

  const player = {
    ctx: null,
    analyser: null,
    nextAt: 0,
    sources: new Set(),
    ending: false,
    ensure() {
      if (!this.ctx) {
        this.ctx = new AudioContext();
        this.analyser = this.ctx.createAnalyser();
        this.analyser.fftSize = 256;
        this.analyser.connect(this.ctx.destination);
      }
      if (this.ctx.state === "suspended") this.ctx.resume();
    },
    start() {
      this.ensure();
      this.stop(false);
      this.ending = false;
      this.nextAt = this.ctx.currentTime;
    },
    play(samples, rate) {
      this.ensure();
      const buffer = this.ctx.createBuffer(1, samples.length, rate);
      buffer.copyToChannel(samples, 0);
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.analyser);
      const at = Math.max(this.ctx.currentTime, this.nextAt);
      source.start(at);
      this.nextAt = at + buffer.duration;
      this.sources.add(source);
      source.onended = () => {
        this.sources.delete(source);
        if (this.ending && this.sources.size === 0) send({ type: "played" });
      };
    },
    end() {
      this.ending = true;
      if (this.sources.size === 0) send({ type: "played" });
    },
    stop(report = true) {
      for (const s of this.sources) { try { s.stop(); } catch {} }
      this.sources.clear();
      this.ending = false;
      if (report) orb.level = 0;
    },
    level() {
      if (!this.analyser || this.sources.size === 0) return 0;
      const data = new Uint8Array(this.analyser.fftSize);
      this.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
      return Math.min(1, Math.sqrt(sum / data.length) * 3);
    },
  };

  function onAudio(buffer) {
    const rate = new DataView(buffer).getUint32(0, true);
    player.play(new Float32Array(buffer, 4), rate);
  }

  // ---- push-to-talk ----

  const ptt = {
    ctx: null,
    stream: null,
    node: null,
    holding: false,
    micLevel: 0,
    live() {
      const track = this.stream && this.stream.getAudioTracks()[0];
      return !!track && track.readyState === "live" && !track.muted;
    },
    release_mic() {
      // A release also abandons an open still in flight: it notices on its next step and starts over.
      this.generation += 1;
      this.opening = null;
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) this.ctx.close().catch(() => {});
      this.stream = this.ctx = this.node = null;
    },
    generation: 0,
    opening: null,
    ensure() {
      if (this.node && this.live()) return Promise.resolve(true);
      // One open at a time: the 3 s retry, a device change and a press can all call this while an open is still
      // awaiting the microphone, and each overlapping open left a stream behind that kept sending frames.
      if (!this.opening) {
        const opening = this.open().finally(() => { if (this.opening === opening) this.opening = null; });
        this.opening = opening;
      }
      return this.opening;
    },
    async open() {
      this.release_mic();
      const generation = this.generation;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      } catch (err) {
        notice(`microphone not available: ${err.message}`, "error");
        return false;
      }
      let ctx = null, node;
      const drop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (ctx) ctx.close().catch(() => {});
      };
      try {
        if (generation !== this.generation) { drop(); return this.ensure(); }
        ctx = new AudioContext({ sampleRate: 16000 });
        await ctx.audioWorklet.addModule(`/static/ptt-worklet.js?token=${encodeURIComponent(token)}`);
        if (generation !== this.generation) { drop(); return this.ensure(); }
        const source = ctx.createMediaStreamSource(stream);
        node = new AudioWorkletNode(ctx, "ptt");
        node.port.onmessage = (ev) => {
          if (!this.holding && !orb.wake) return;
          this.micLevel = ev.data.level;
          if (orb.ws && orb.ws.readyState === WebSocket.OPEN) orb.ws.send(ev.data.frame.buffer);
        };
        source.connect(node);
      } catch (err) {
        // Nothing is committed yet, so release_mic() would not reach these; left open, every retry leaked one.
        drop();
        notice(`microphone not available: ${err.message}`, "error");
        return false;
      }
      this.stream = stream;
      this.ctx = ctx;
      this.node = node;
      return true;
    },
    async hold() {
      if (this.holding) return;
      const refusal = !orb.voice ? "speech engines are not loaded"
        : !orb.attached && !orb.enrolling ? orb.attachMessage || "not attached to an agent session"
        : orb.state !== "idle" && orb.state !== "thinking" ? `busy: ${orb.state}` : null;
      if (refusal) { notice(`can't listen: ${refusal}`, "error"); return; }
      if (!(await this.ensure())) return;
      if (this.ctx.state === "suspended") await this.ctx.resume();
      this.holding = true;
      send({ type: "ptt_start" });
    },
    release() {
      if (!this.holding) return;
      this.holding = false;
      this.micLevel = 0;
      send({ type: "ptt_stop" });
    },
  };

  // A device change drops the stream; with the wake word on the mic is wanted back at once, and a stream that
  // dies for any other reason is reopened on the next check.
  if (navigator.mediaDevices) navigator.mediaDevices.addEventListener("devicechange", () => { ptt.release_mic(); if (orb.wake) ptt.ensure(); });
  setInterval(() => { if (orb.wake && !ptt.live()) ptt.ensure(); }, 3000);

  const mic = $("mic");
  mic.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    if (orb.state === "idle" || orb.state === "thinking") { mic.setPointerCapture(ev.pointerId); ptt.hold(); }
    else if (orb.state !== "listening") stopNow();
  });
  $("stop").addEventListener("click", () => {
    orb.stopping = true;
    updateMic();
    stopNow();
    setTimeout(() => { orb.stopping = false; updateMic(); }, 4000);  // the notice says how it went
  });
  // Stop silences this window at once, then tells the server, which cancels speech before anything else.
  function stopNow() {
    player.stop();
    send({ type: "stop" });
  }
  mic.addEventListener("pointerup", () => ptt.release());
  mic.addEventListener("pointercancel", () => ptt.release());

  function inTextField(target) {
    return target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
  }
  addEventListener("keydown", (ev) => {
    if (ev.code !== orb.pttKey || ev.repeat || inTextField(ev.target)) return;
    ev.preventDefault();
    if (orb.state === "idle" || orb.state === "thinking") ptt.hold();
    else if (orb.state !== "listening") stopNow();
  });
  addEventListener("keyup", (ev) => {
    if (ev.code !== orb.pttKey || inTextField(ev.target)) return;
    ev.preventDefault();
    ptt.release();
  });
  addEventListener("blur", () => ptt.release());

  // ---- panels ----

  const panelsBridge = {
    seq: 0,
    window: Math.random().toString(36).slice(2),  // action answers go to every window; this one takes its own
    pending: new Map(), // ref -> iframe window
    frameOf(source) {
      for (const [kind, pages] of [["panel", orb.panels], ["widget", orb.widgets]]) {
        const found = [...pages.entries()].find(([, p]) => p.iframe && p.iframe.contentWindow === source);
        if (found) return { kind, name: found[0], iframe: found[1].iframe };
      }
      return null;
    },
    handle(ev) {
      const m = ev.data || {};
      if (!["send", "speak", "stop", "action", "key"].includes(m.orb)) return;
      const frame = this.frameOf(ev.source);
      if (!frame) return;
      if (m.orb === "stop") { stopNow(); return; }
      if (m.orb === "key") { if (m.code === "Escape") escapeKey(); return; }
      const active = !!(navigator.userActivation && navigator.userActivation.isActive) && document.activeElement === frame.iframe;
      if (m.orb === "action") {
        const ref = `action:${this.window}:${++this.seq}`;
        this.pending.set(ref, { source: ev.source, id: m.id });
        send({ type: "action", kind: frame.kind, page: frame.name, name: String(m.name || ""), args: m.args ?? null, active, ref });
        return;
      }
      if (m.orb === "speak") { if (active && orb.state === "idle") send({ type: "speak_text", text: String(m.text || ""), active }); return; }
      if (!active) { ev.source.postMessage({ orb: "reject", id: m.id, error: "orb.send works only during a click or key press" }, "*"); return; }
      const ref = `panel:${this.window}:${++this.seq}`;
      this.pending.set(ref, { source: ev.source, id: m.id });
      send({ type: "ask", text: String(m.text || ""), speak: m.speak !== false, ref, active, kind: frame.kind, page: frame.name });
    },
    resolve(ref, text) {
      const p = ref && this.pending.get(ref);
      if (!p) return;
      this.pending.delete(ref);
      p.source.postMessage({ orb: "reply", id: p.id, text }, "*");
    },
    answer(m) {
      const p = m.ref && this.pending.get(m.ref);
      if (!p) return;
      this.pending.delete(m.ref);
      p.source.postMessage(m.ok ? { orb: "result", id: p.id, result: m.result } : { orb: "reject", id: p.id, error: m.error }, "*");
    },
    reject(ref, error) {
      const p = ref && this.pending.get(ref);
      if (!p) return;
      this.pending.delete(ref);
      p.source.postMessage({ orb: "reject", id: p.id, error }, "*");
    },
    broadcastState(state) {
      for (const pages of [orb.panels, orb.widgets]) {
        for (const p of pages.values()) if (p.iframe) p.iframe.contentWindow.postMessage({ orb: "state", state }, "*");
      }
    },
  };
  addEventListener("message", (ev) => panelsBridge.handle(ev));

  function sandboxedFrame(url) {
    const iframe = document.createElement("iframe");
    iframe.sandbox = "allow-scripts allow-forms allow-popups allow-modals";
    iframe.referrerPolicy = "no-referrer";
    iframe.src = url;
    return iframe;
  }

  function postData(p) {
    if (p && p.iframe && p.data !== null && p.data !== undefined) {
      p.iframe.contentWindow.postMessage({ orb: "data", data: p.data, at: p.data_at }, "*");
    }
  }

  function paneOf(w) {
    return w.pane_width > 0 && w.pane_height > 0 ? { pane_width: w.pane_width, pane_height: w.pane_height } : {};
  }

  const widgetsUi = {
    installed: [],
    missing: [],
    paneRect() {
      const r = $("orb-pane").getBoundingClientRect();
      return { width: r.width, height: r.height };
    },
    snapshot(info) {
      for (const w of (info && info.shown) || []) this.show(w);
      for (const name of [...orb.widgets.keys()]) {
        if (!((info && info.shown) || []).some((w) => w.name === name)) this.close(name);
      }
      this.lists(info);
    },
    lists(info) {
      this.installed = (info && info.installed) || [];
      this.missing = (info && info.missing) || [];
      this.renderList();
    },
    show(w) {
      const existing = orb.widgets.get(w.name);
      const size = { width: w.width, height: w.height };
      const placed = w.x != null && w.y != null;
      if (existing) {
        Object.assign(existing, { data: w.data, data_at: w.data_at, description: w.description, corner: w.corner, size });
        existing.place = placed ? { x: w.x, y: w.y, ...size, ...paneOf(w) } : null;
        existing.iframe.src = `${w.url}?v=${Date.now()}`;
        this.stale(w.name, w.feed_error);
        this.failed(w.name, w.page_error);
        this.apply(w.name);
        return;
      }
      const frame = document.createElement("div");
      frame.className = "widget";
      const edges = ["top", "left", "right", "bottom"].map((side) => {
        const edge = document.createElement("div");
        edge.className = side === "top" ? "widget-edge" : `widget-side widget-${side}`;
        edge.title = `${w.name}: drag to move`;
        return edge;
      });
      const iframe = sandboxedFrame(w.url);
      iframe.addEventListener("load", () => postData(orb.widgets.get(w.name)));
      const grip = document.createElement("div");
      grip.className = "widget-grip";
      grip.title = "drag to resize";
      frame.append(edges[0], iframe, ...edges.slice(1), grip);
      $("widgets").appendChild(frame);
      orb.widgets.set(w.name, { name: w.name, description: w.description, data: w.data, data_at: w.data_at, corner: w.corner, size,
                                place: placed ? { x: w.x, y: w.y, ...size, ...paneOf(w) } : null, refPane: null, frame, iframe, edges, feed_error: null, page_error: null });
      for (const edge of edges) this.drag(w.name, edge, (start, dx, dy) => ({ ...start, left: start.left + dx, top: start.top + dy }));
      this.drag(w.name, grip, (start, dx, dy) => ({ ...start, width: start.width + dx, height: start.height + dy }));
      this.stale(w.name, w.feed_error);
      this.failed(w.name, w.page_error);
      this.apply(w.name);
      this.renderList();
    },
    mark(name) {
      const p = orb.widgets.get(name);
      p.frame.classList.toggle("failed", !!p.page_error);
      p.frame.classList.toggle("stale", !p.page_error && !!p.feed_error);
      const reason = p.page_error || (p.feed_error && `not refreshed: ${p.feed_error}`);
      for (const edge of p.edges) edge.title = reason ? `${name}: ${reason}` : `${name}: drag to move`;
    },
    failed(name, error) {
      const p = orb.widgets.get(name);
      if (!p) return;
      p.page_error = error || null;
      this.mark(name);
    },
    drag(name, handle, rectAfter) {
      handle.addEventListener("pointerdown", (ev) => {
        const p = orb.widgets.get(name);
        if (!p || ev.button !== 0) return;
        ev.preventDefault();
        const pane = this.paneRect();
        const from = this.placeOf(p, pane);
        if (from === null) return;
        handle.setPointerCapture(ev.pointerId);
        p.frame.classList.add("dragging");
        const start = toPixels(from, pane);
        const origin = { x: ev.clientX, y: ev.clientY };
        const onMove = (e) => {
          p.place = { ...clampPlace(fromPixels(rectAfter(start, e.clientX - origin.x, e.clientY - origin.y), pane), pane),
                      pane_width: pane.width, pane_height: pane.height };
          this.apply(name);
        };
        const onUp = () => {
          handle.removeEventListener("pointermove", onMove);
          handle.removeEventListener("pointerup", onUp);
          p.frame.classList.remove("dragging");
          if (p.place !== null) send({ type: "widget_place", name, ...p.place });
        };
        handle.addEventListener("pointermove", onMove);
        handle.addEventListener("pointerup", onUp);
      });
    },
    // The widget's size is the one it was placed at, scaled by how the pane has changed since. A place that does not
    // say its pane (saved before widgets scaled, or a corner it never left) takes the first pane this window saw as
    // its reference, so nothing jumps.
    placeOf(p, pane) {
      if (p.refPane === null && pane.width > 0 && pane.height > 0) p.refPane = pane;
      const reference = referenceOf(p.place) || p.refPane;
      if (p.place === null) return cornerPlace(p.corner, scaledSize(p.size, reference, pane), pane);
      return clampPlace(scaledPlace(p.place, reference, pane), pane);
    },
    apply(name) {
      const p = orb.widgets.get(name);
      if (!p) return;
      const pane = this.paneRect();
      const place = this.placeOf(p, pane);
      if (place === null) return;
      const px = toPixels(place, pane);
      Object.assign(p.frame.style, { left: `${px.left}px`, top: `${px.top}px`, width: `${px.width}px`, height: `${px.height}px` });
    },
    layout() {
      for (const name of orb.widgets.keys()) this.apply(name);
    },
    placed(m) {
      const p = orb.widgets.get(m.name);
      if (!p) return;
      p.place = { x: m.x, y: m.y, width: m.width, height: m.height, ...paneOf(m) };
      this.apply(m.name);
    },
    data(name, data, at) {
      const p = orb.widgets.get(name);
      if (!p) return;
      Object.assign(p, { data, data_at: at });
      postData(p);
    },
    stale(name, error) {
      const p = orb.widgets.get(name);
      if (!p) return;
      p.feed_error = error || null;
      this.mark(name);
    },
    close(name) {
      const p = orb.widgets.get(name);
      if (!p) return;
      p.frame.remove();
      orb.widgets.delete(name);
      this.renderList();
    },
    renderList() {
      const any = this.installed.length > 0 || this.missing.length > 0;
      $("bar-widgets").hidden = !any;
      $("bar-widgets").classList.toggle("on", orb.widgets.size > 0);
      if (!any) $("widgets-list").hidden = true;
      const rows = this.installed.map((w) => this.row(w.name, `[${w.scope}]`, w.description, orb.widgets.has(w.name), false));
      rows.push(...this.missing.map((m) => this.row(m.name, "not shown", `${m.reason}; click to turn it off`, false, true)));
      $("widgets-list").replaceChildren(...rows);
    },
    row(name, mark, title, on, missing) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `widget-row${missing ? " missing" : ""}`;
      row.title = title;
      for (const [cls, text] of [["widget-tick", on ? "✓" : ""], ["widget-name", name], ["widget-scope", mark]]) {
        const cell = document.createElement("span");
        cell.className = cls;
        cell.textContent = text;
        row.appendChild(cell);
      }
      row.addEventListener("click", (ev) => {
        ev.stopPropagation();
        send({ type: "widget", name, on: missing ? false : !on });
      });
      return row;
    },
  };
  $("bar-widgets").addEventListener("click", (ev) => { ev.stopPropagation(); $("widgets-list").hidden = !$("widgets-list").hidden; });

  const VIEW_KEY = "orb-view";
  function storedView() {
    try {
      const mode = sessionStorage.getItem(VIEW_KEY);
      return ["split", "orb", "panel"].includes(mode) ? mode : "split";
    } catch { return "split"; }
  }
  const panelsUi = {
    show(p, { restoring = false } = {}) {
      const existing = orb.panels.get(p.name);
      if (existing) {
        existing.tab.querySelector(".tab-title").textContent = p.title;
        Object.assign(existing, { data: p.data, data_at: p.data_at });
        this.stale(p.name, p.feed_error);
        if (existing.iframe) existing.iframe.src = `${p.url}?v=${Date.now()}`;
        if (!p.in_place) this.activate(p.name);
        releasesUi.render();
        return;
      }
      const tab = document.createElement("button");
      tab.className = "tab";
      tab.innerHTML = `<span class="tab-title"></span><span class="tab-close" title="close">×</span>`;
      tab.querySelector(".tab-title").textContent = p.title;
      tab.addEventListener("click", (ev) => {
        if (ev.target.classList.contains("tab-close")) { send({ type: "panel_close", name: p.name }); this.close(p.name); }
        else this.activate(p.name);
      });
      $("tabs").appendChild(tab);
      let iframe = null, box;
      if (p.builtin) {
        box = document.createElement("div");
        box.className = "panel-body builtin";
        box.id = `builtin-${p.name}`;
      } else {
        iframe = sandboxedFrame(p.url);
        iframe.className = "panel-body";
        // Every load, the first and each reload, starts with no data: the page gets the latest the orb holds.
        iframe.addEventListener("load", () => this.post(p.name));
        box = iframe;
      }
      $("panel-bodies").appendChild(box);
      orb.panels.set(p.name, { ...p, tab, iframe, box });
      this.stale(p.name, p.feed_error);
      if (this.mode === "orb" && !restoring) this.setMode("split");
      this.activate(p.name);
      transcriptUi.render();
      releasesUi.render();
    },
    data(name, data, at) {
      const p = orb.panels.get(name);
      if (!p) return;
      Object.assign(p, { data, data_at: at });
      this.post(name);
    },
    post(name) {
      postData(orb.panels.get(name));
    },
    stale(name, error) {
      const p = orb.panels.get(name);
      if (!p) return;
      p.tab.classList.toggle("stale", !!error);
      p.tab.title = error ? `not refreshed: ${error}` : "";
    },
    activate(name) {
      orb.activePanel = name;
      for (const [n, p] of orb.panels) {
        p.tab.classList.toggle("active", n === name);
        p.box.hidden = n !== name;
      }
      this.layout();
    },
    close(name) {
      const p = orb.panels.get(name);
      if (!p) return;
      p.tab.remove();
      p.box.remove();
      orb.panels.delete(name);
      if (orb.activePanel === name) this.activate([...orb.panels.keys()].pop() || null);
      this.layout();
    },
    // "split" shares the window, "orb" hides the panels, "panel" docks the orb to a strip. Only meaningful
    // while panels are open; a panel the agent shows while they are hidden brings them back.
    // Kept for this window across reloads (a new skin or theme reloads the page); a new window starts split.
    mode: storedView(),
    setMode(mode) {
      this.mode = mode;
      try { sessionStorage.setItem(VIEW_KEY, mode); } catch {}
      this.layout();
    },
    layout() {
      const any = orb.panels.size > 0;
      $("transcript-toggle").classList.toggle("on", orb.panels.has("transcript"));
      $("releases-toggle").classList.toggle("on", orb.panels.has("releases"));
      $("panels").hidden = !any;
      const stage = $("stage");
      stage.classList.toggle("has-panels", any);
      for (const m of ["split", "orb", "panel"]) stage.classList.toggle(`mode-${m}`, any && this.mode === m);
      document.body.classList.toggle("orb-hidden", any && this.mode === "panel");
      $("divider").hidden = !any;
    },
    toggle(mode) {
      this.setMode(this.mode === mode ? "split" : mode);
    },
    shownMode: "split",
    hideOrRestore() {
      if (this.mode === "orb") { this.setMode(this.shownMode); return; }
      this.shownMode = this.mode;
      this.setMode("orb");
    },
  };
  // Gestures, no buttons: double-click the orb for a full orb, double-click a panel's tab for a full panel, and
  // either again for the split. The divider drags; dropped near an edge it gives that side the window.
  // On the pane, not the canvas: a WebGL skin gets a fresh canvas element, and a listener on the old one goes with it.
  $("orb-pane").addEventListener("dblclick", (ev) => { if (ev.target.tagName === "CANVAS" && orb.panels.size) panelsUi.toggle("orb"); });
  $("tabs").addEventListener("dblclick", (ev) => { if (ev.target.closest(".tab")) panelsUi.toggle("panel"); });
  // Escape: back to the split view, else stop. Returns whether it did something.
  function escapeKey() {
    if (panelsUi.mode !== "split") { panelsUi.setMode("split"); return true; }
    if (orb.state !== "idle" && orb.state !== "listening") { stopNow(); return true; }
    return false;
  }
  addEventListener("keydown", (ev) => {
    if (ev.code !== "Escape" || inTextField(ev.target)) return;
    if (escapeKey()) ev.preventDefault();
  });
  const divider = $("divider");
  const SNAP = 0.12;
  divider.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    divider.setPointerCapture(ev.pointerId);
    $("stage").classList.add("dragging");
    const stage = $("stage").getBoundingClientRect();
    // A drag that snaps to an edge hands that side the window; the split it leaves behind is the one from before
    // the drag, so Escape or a double-click brings the panels back at a usable width rather than at the edge.
    const before = $("stage").style.getPropertyValue("--orb-basis");
    let share = null;
    const move = (e) => {
      share = Math.min(1, Math.max(0, (e.clientX - stage.left) / stage.width));
      $("stage").style.setProperty("--orb-basis", `${(share * 100).toFixed(1)}%`);
      if (panelsUi.mode !== "split") panelsUi.setMode("split");
      placeBar();
      widgetsUi.layout();
    };
    const up = () => {
      divider.removeEventListener("pointermove", move);
      divider.removeEventListener("pointerup", up);
      $("stage").classList.remove("dragging");
      const snapped = share !== null && (share < SNAP || share > 1 - SNAP);
      if (snapped) {
        if (before) $("stage").style.setProperty("--orb-basis", before);
        else $("stage").style.removeProperty("--orb-basis");
      }
      if (share !== null && share < SNAP) panelsUi.setMode("panel");
      else if (share !== null && share > 1 - SNAP) panelsUi.setMode("orb");
      resize();
    };
    divider.addEventListener("pointermove", move);
    divider.addEventListener("pointerup", up);
  });

  async function toggleBuiltin(name, title) {
    if (orb.panels.has(name)) {
      send({ type: "panel_close", name });
      panelsUi.close(name);
      return;
    }
    const r = await fetch("/panel", {
      method: "POST",
      headers: { "X-Orb-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({ name, title }),
    });
    if (!r.ok) notice(`${name}: ${await r.text()}`, "error");
  }
  const toggleTranscript = () => toggleBuiltin("transcript", "Transcript");
  const toggleReleases = () => toggleBuiltin("releases", "Release notes");
  $("transcript-toggle").addEventListener("click", toggleTranscript);
  $("releases-toggle").addEventListener("click", toggleReleases);

  function savedReleasesTextSize() {
    try { return clampTextSize(Number(localStorage.getItem("orb.releasesTextSize"))); } catch (e) { return TEXT_SIZES.start; }
  }
  function saveReleasesTextSize(size) {
    try { localStorage.setItem("orb.releasesTextSize", String(size)); } catch (e) { return; }
  }

  const releasesUi = {
    size: savedReleasesTextSize(),
    resize(direction) {
      this.size = nextTextSize(this.size, direction);
      saveReleasesTextSize(this.size);
      this.render();
    },
    render() {
      const box = $("builtin-releases"), p = orb.panels.get("releases");
      if (!box || !p) return;
      const rows = releaseRows(p.data);
      box.style.fontSize = `${this.size}px`;
      box.replaceChildren(this.controls());
      if (!rows.length) { box.append("No release notes were found."); return; }
      rows.forEach((row, i) => box.appendChild(this.row(row, `row${i}`)));
    },
    controls() {
      const bar = document.createElement("div");
      bar.className = "rn-zoom";
      for (const [text, direction, name] of [["A−", -1, "Smaller text"], ["A+", 1, "Larger text"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.title = name;
        button.setAttribute("aria-label", name);
        button.addEventListener("click", () => this.resize(direction));
        bar.appendChild(button);
      }
      return bar;
    },
    row({ kind, text, request, stripe }, id) {
      const element = document.createElement("div");
      element.className = `rn-${kind}${stripe ? " rn-stripe" : ""}`;
      const label = document.createElement(kind === "release" ? "h3" : "span");
      label.textContent = text;
      element.appendChild(label);
      if (request) element.appendChild(this.explainButton(id, request));
      return element;
    },
    explaining: null,
    seq: 0,
    explainButton(id, request) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rn-explain tr-say";
      button.dataset.id = id;
      button.addEventListener("click", () => this.toggle(id, request));
      this.show(button);
      return button;
    },
    show(button) {
      const mine = !!this.explaining && this.explaining.id === button.dataset.id;
      button.classList.toggle("stop", mine);
      button.setAttribute("aria-label", mine ? "Stop explaining" : "Explain this aloud");
    },
    syncButtons() {
      for (const button of document.querySelectorAll("#builtin-releases .rn-explain")) this.show(button);
    },
    toggle(id, request) {
      const stopping = !!this.explaining && this.explaining.id === id;
      if (this.explaining) stopNow();
      if (!stopping) {
        this.explaining = { id, ref: `releases:${++this.seq}`, replied: false };
        send({
          type: "ask", text: request, speak: true, ref: this.explaining.ref, kind: "panel", page: "releases",
          active: !!(navigator.userActivation && navigator.userActivation.isActive),
        });
      }
      this.syncButtons();
    },
    replied(ref) {
      if (this.explaining && this.explaining.ref === ref) this.explaining.replied = true;
      this.settle();
    },
    ended(ref) {
      if (this.explaining && this.explaining.ref === ref) {
        this.explaining = null;
        this.syncButtons();
      }
    },
    settle() {
      if (this.explaining && this.explaining.replied && orb.state === "idle") this.explaining = null;
      this.syncButtons();
    },
  };

  const wakeUi = {
    async apply(info) {
      orb.wake = info && !info.reason ? info : null;
      if (info && info.reason) notice(`wake word off: ${info.reason}`, "error");
      $("bar-wake").hidden = !orb.wake;
      if (orb.wake) {
        $("bar-wake").textContent = `listening for “${orb.wake.phrase}”${orb.wake.voice_auth ? " · your voice only" : ""}`;
        if (await ptt.ensure()) { if (ptt.ctx.state === "suspended") ptt.ctx.resume(); }
      }
      updateMic();
    },
  };

  const enrollUi = {
    apply(info) {
      orb.enrolling = info || null;
      if (info) notice(`read aloud while holding: “${info.phrase}”`);
      updateMic();
    },
  };

  function showVoice(on) {
    orb.speak = on;
    const button = $("voice-toggle");
    button.classList.toggle("on", !on);
    button.textContent = on ? "voice" : "muted";
    button.title = `${on ? "Mute" : "Unmute"} the orb's speech (${(orb.muteKey || "KeyM").replace(/^Key/, "")})`;
  }
  $("voice-toggle").addEventListener("click", () => send({ type: "voice", on: !orb.speak }));
  addEventListener("keydown", (ev) => {
    if (ev.code !== orb.muteKey || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target)) return;
    ev.preventDefault();
    send({ type: "voice", on: !orb.speak });
  });
  addEventListener("keydown", (ev) => {
    if (ev.code !== "KeyT" || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target)) return;
    ev.preventDefault();
    toggleTranscript();
  });
  addEventListener("keydown", (ev) => {
    if (ev.code !== "KeyR" || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target)) return;
    ev.preventDefault();
    toggleReleases();
  });
  addEventListener("keydown", (ev) => {
    if (ev.code !== orb.panelsKey || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target)) return;
    ev.preventDefault();
    if (!orb.panels.size) { notice("no panels open"); return; }
    panelsUi.hideOrRestore();
  });

  const transcriptUi = {
    answer(req, text, status) {
      const entry = orb.transcript.find((e) => e.req === req) || orb.transcript.find((e) => e.status === "pending");
      if (entry) { entry.req = req; entry.reply = text; entry.status = status; }
      else orb.transcript.push({ req, reply: text, status });
      this.render();
    },
    render() {
      const box = $("builtin-transcript");
      if (!box) return;
      this.watchScroll(box);
      if (orb.session.feed.length) { this.renderFeed(box); return; }
      this.rows.clear();
      box.innerHTML = "";
      for (const e of orb.transcript) {
        const row = document.createElement("div");
        row.className = `tr-row ${e.status || ""}`;
        const q = document.createElement("div"); q.className = "tr-q"; q.textContent = e.request || "";
        const a = document.createElement("div"); a.className = "tr-a";
        a.textContent = e.status === "interrupted" ? "— interrupted —" : e.status === "failed" ? "— failed —" : (e.reply || "…");
        row.append(q, a);
        if (e.reply) row.appendChild(this.speaker(`req:${e.req}`, e.reply));
        box.appendChild(row);
      }
      box.scrollTop = box.scrollHeight;
    },
  };

  Object.assign(transcriptUi, {
    // The session's own feed: what was typed (here or in the terminal), what the agent said, and each tool call on
    // its own line with its result folded away. A row is rebuilt only when its item or its shown time changed, so a row
    // the reader has opened stays open while new ones arrive.
    rows: new Map(),
    opened: new Set(),
    renderFeed(box) {
      const keep = new Set();
      let at = 0;
      const place = (key, sig, build) => {
        let row = this.rows.get(key);
        if (!row || row.sig !== sig) {
          const el = build();
          if (row) row.el.replaceWith(el);
          row = { sig, el };
          this.rows.set(key, row);
        }
        keep.add(key);
        if (box.children[at] !== row.el) box.insertBefore(row.el, box.children[at] || null);
        at += 1;
      };
      const days = dateLines(orb.session.feed);
      orb.session.feed.forEach((item, i) => {
        if (days[i]) place(`day:${item.id}`, days[i], () => this.buildDay(days[i]));
        place(item.id, JSON.stringify(item) + entryTime(item.ts), () => this.buildRow(item));
      });
      for (const [id, row] of this.rows) if (!keep.has(id)) { row.el.remove(); this.rows.delete(id); this.opened.delete(id); }
      const live = this.liveLine(box);
      if (box.lastElementChild !== live) box.appendChild(live);
      this.tickLive();
      if (this.following) box.scrollTop = box.scrollHeight;
    },
    following: true,
    watchScroll(box) {
      if (box.dataset.watched) return;
      box.dataset.watched = "1";
      box.addEventListener("scroll", () => { this.following = nearBottom(box.scrollTop, box.clientHeight, box.scrollHeight); });
    },
    buildDay(label) {
      const line = document.createElement("div");
      line.className = "tr-day";
      line.textContent = label;
      return line;
    },
    stamp(parent, item) {
      const time = entryTime(item.ts);
      if (!time) return;
      const el = document.createElement("span");
      el.className = "tr-time";
      el.textContent = time;
      parent.appendChild(el);
    },
    buildRow(item) {
      if (item.kind === "step") return this.buildStep(item);
      const row = document.createElement("div");
      row.className = `tr-row ${item.kind}${item.kind === "reply" && !item.final ? " note" : ""}`;
      this.stamp(row, item);
      const body = document.createElement("div");
      body.className = item.kind === "you" ? "tr-q" : "tr-a";
      if (item.kind === "reply") { body.classList.add("md"); body.innerHTML = markdown(item.text); } else body.textContent = item.text;
      if (item.kind === "you" && item.orb) body.title = "sent from the orb";
      if (item.kind === "reply") {
        body.classList.toggle("open", this.opened.has(item.id));
        body.addEventListener("click", () => { body.classList.toggle("open"); this.remember(item.id, body.classList.contains("open")); });
      }
      row.appendChild(body);
      if (item.kind === "reply") row.appendChild(this.speaker(item.id, item.text));
      return row;
    },
    saying: null,
    canSay: false,
    speaker(id, text) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tr-say";
      button.dataset.id = id;
      button.addEventListener("click", () => {
        if (this.saying === id) { stopNow(); return; }
        send({ type: "speak_text", text, said: id, active: !!(navigator.userActivation && navigator.userActivation.isActive) });
      });
      this.syncSpeaker(button);
      return button;
    },
    syncSpeaker(button) {
      const mine = this.saying === button.dataset.id;
      button.hidden = !this.canSay;
      button.classList.toggle("stop", mine);
      button.disabled = !mine && orb.state !== "idle";
      button.setAttribute("aria-label", mine ? "Stop reading" : "Read this reply aloud");
    },
    syncSpeakers() {
      for (const button of document.querySelectorAll("#builtin-transcript .tr-say")) this.syncSpeaker(button);
    },
    buildStep(item) {
      const details = document.createElement("details");
      details.className = `tr-step${item.error ? " error" : ""}${"result" in item ? "" : " running"}`;
      details.open = this.opened.has(item.id);
      const summary = document.createElement("summary");
      this.stamp(summary, item);
      summary.append(item.text);
      details.appendChild(summary);
      if ("result" in item) {
        const result = document.createElement("pre");
        result.textContent = item.result || "(no output)";
        details.appendChild(result);
      }
      details.addEventListener("toggle", () => this.remember(item.id, details.open));
      return details;
    },
    remember(id, open) {
      if (open) this.opened.add(id); else this.opened.delete(id);
    },
    // The line under the last row while a turn runs: that it is working, for how long, and on what.
    liveLine(box) {
      let live = $("tr-live");
      if (!live) { live = document.createElement("div"); live.id = "tr-live"; live.className = "tr-live"; }
      return live;
    },
    tickLive() {
      const live = $("tr-live");
      if (!live) return;
      const s = orb.session;
      live.hidden = !s.busy;
      if (s.busy) live.textContent = ["working", elapsed(s.since, Date.now()), s.doing].filter(Boolean).join(" · ");
    },
  });
  let shownZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  setInterval(() => {
    transcriptUi.tickLive();
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone !== shownZone) { shownZone = zone; transcriptUi.render(); }
  }, 1000);

  // ---- skin and the orb canvas ----

  let canvas = $("orb");
  let ctx = canvas.getContext("2d");
  function freshCanvas(kind) {
    // A canvas keeps its first context for life, so a change of kind means a new element in its place.
    const next = canvas.cloneNode();
    canvas.replaceWith(next);
    canvas = next;
    ctx = canvas.getContext(kind === "webgl" ? "webgl2" : "2d");
    resize();
    return ctx;
  }
  function resize() {
    const pane = canvas.parentElement.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(pane.width * devicePixelRatio));
    canvas.height = Math.max(1, Math.floor(pane.height * devicePixelRatio));
    placeBar();
    widgetsUi.layout();
  }
  // With the panels beside the orb, the pill sits in the orb's corner rather than over the panels, where it read as
  // part of a panel; opened, it wraps within the orb's side. Full orb or full panels, it goes back to the window's.
  function placeBar() {
    const bar = document.querySelector(".bar");
    const stage = $("stage");
    const onOrb = stage.classList.contains("has-panels") && stage.classList.contains("mode-split");
    document.body.classList.toggle("bar-on-orb", onOrb);
    if (!onOrb) { bar.style.right = ""; bar.style.maxWidth = ""; return; }
    const pane = $("orb-pane").getBoundingClientRect();
    bar.style.right = `${Math.max(12, innerWidth - pane.right + 12)}px`;
    bar.style.maxWidth = `${Math.max(120, pane.width - 24)}px`;
  }
  addEventListener("resize", resize);
  let paneResized = false;
  new ResizeObserver(() => { paneResized = true; }).observe(canvas.parentElement);
  resize();

  const STAND_IN = { wake_word: "listening", working: "thinking", waiting: "idle" };
  const COLORS = { idle: "#5ad1ff", listening: "#7ee787", transcribing: "#c9a8ff", thinking: "#ffb454", speaking: "#ff7ab6" };
  const skin = {
    draw: null,
    info: null,
    values: {},
    load(info) {
      if (this.info && this.info.name === info.name && this.info.version === info.version) {
        // the same skin again, on a reconnect: keep the running one, take the server's choices
        this.values = info.values || {};
        skinOptions.render(info);
        return;
      }
      this.info = info;
      this.values = info.values || {};
      if (info.fallback_reason) notice(`skin: ${info.fallback_reason}; using pulse`, "error");
      skinOptions.render(info);
      if (info.canvas === "webgl" && !freshCanvas("webgl")) {
        this.giveUp("this browser has no WebGL2");
        return;
      }
      const script = document.createElement("script");
      script.src = `/skin/skin.js?token=${encodeURIComponent(token)}&skin=${encodeURIComponent(info.name)}`;
      script.onload = () => this.start();
      script.onerror = () => this.giveUp("skin.js failed to load");
      document.head.appendChild(script);
    },
    async start() {
      const s = window.orbSkin;
      if (!s || typeof s.draw !== "function") { this.giveUp("skin.js did not set window.orbSkin.draw"); return; }
      try {
        if (typeof s.init === "function") {
          await s.init(canvas, ctx, { options: this.values, url: (path) => `/skin/${path}?token=${encodeURIComponent(token)}` });
        }
        this.draw = s.draw;
      } catch (err) {
        this.giveUp(`skin init threw (${err.message})`);
      }
    },
    giveUp(why) {
      this.draw = null;
      if (this.info && this.info.canvas === "webgl") freshCanvas("2d");
      if (this.info) this.info.canvas = "2d";
      notice(`${why}; using the built-in orb`, "error");
    },
    shown(state) {
      const states = this.info ? this.info.states : null;
      if (!states) return state;
      while (!states.includes(state)) state = STAND_IN[state] || "idle";
      return state;
    },
    fallback(ctx, { state, level, time, width, height, scale }) {
      const cx = width / 2, cy = height / 2, base = Math.min(width, height) * 0.18 * scale;
      ctx.clearRect(0, 0, width, height);
      const r = base * (1 + Math.sin(time / 900) * 0.04 + level * 0.5);
      const g = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r);
      g.addColorStop(0, COLORS[state] || COLORS.idle);
      g.addColorStop(1, "rgba(7,9,15,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
    },
  };

  const skinOptions = {
    render(info) {
      const host = $("skin-options");
      host.textContent = "";
      for (const [name, option] of Object.entries(info.options || {})) {
        const label = document.createElement("label");
        label.title = option.key ? `${option.label} (${option.key.replace(/^Key/, "")})` : option.label;
        label.append(`${option.label} `);
        const select = document.createElement("select");
        select.dataset.option = name;
        for (const choice of option.choices) {
          const o = document.createElement("option");
          o.value = o.textContent = choice;
          o.selected = choice === skin.values[name];
          select.append(o);
        }
        select.addEventListener("change", () => send({ type: "skin_option", option: name, value: select.value }));
        label.append(select);
        host.append(label);
      }
    },
    apply(name, value) {
      skin.values[name] = value;
      const select = $("skin-options").querySelector(`select[data-option="${name}"]`);
      if (select) select.value = value;
    },
    cycle(name) {
      const option = skin.info.options[name];
      const i = option.choices.indexOf(skin.values[name]);
      send({ type: "skin_option", option: name, value: option.choices[(i + 1) % option.choices.length] });
    },
  };
  const engineNames = { stt: [], tts: [] };
  function fillEngineSelect(kind, engines) {
    const select = $(`${kind}-select`);
    select.textContent = "";
    for (const name of engineNames[kind]) {
      const o = document.createElement("option");
      o.value = o.textContent = name;
      o.selected = engines[kind] && name === engines[kind].name;
      select.append(o);
    }
    $(`${kind}-pick`).hidden = !engines[kind] || engineNames[kind].length < 2;
  }
  function showEngines(engines, ttsNames, sttNames) {
    $("engines").textContent = ["stt", "tts"].map((k) => engines[k] ? engines[k].name : "—").join(" · ");
    if (ttsNames) engineNames.tts = ttsNames;
    if (sttNames) engineNames.stt = sttNames;
    fillEngineSelect("tts", engines);
    fillEngineSelect("stt", engines);
    transcriptUi.canSay = !!engines.tts;
    transcriptUi.syncSpeakers();
  }
  $("stt-select").addEventListener("change", (ev) => send({ type: "stt_engine", name: ev.target.value }));
  $("tts-select").addEventListener("change", (ev) => send({ type: "tts_engine", name: ev.target.value }));
  function showVoices(menu) {
    const select = $("voice-select");
    select.textContent = "";
    for (const v of (menu && menu.voices) || []) {
      const o = document.createElement("option");
      o.value = v.id;
      o.textContent = v.label;
      o.selected = v.id === menu.current;
      select.append(o);
    }
    $("voice-pick").hidden = !menu || menu.voices.length < 2;
  }
  $("voice-select").addEventListener("change", (ev) => send({ type: "tts_voice", id: ev.target.value }));

  function withActive(names, active) {
    return names.includes(active) ? names : [...names, active];
  }

  const skinPicker = {
    names: [],
    current: null,
    key: null,
    render(names, current, key) {
      this.names = withActive(names, current);
      this.current = current;
      this.key = key;
      const select = $("skin-select");
      select.textContent = "";
      for (const name of this.names) {
        const o = document.createElement("option");
        o.value = o.textContent = name;
        o.selected = name === current;
        select.append(o);
      }
      $("skin-pick").title = key ? `Skin (${key.replace(/^Key/, "")} cycles)` : "Skin";
      $("skin-pick").hidden = this.names.length < 2;
    },
    cycle() {
      if (this.names.length < 2 || !skin.info) return;
      const i = this.names.indexOf(skin.info.name);
      send({ type: "skin", name: this.names[(i + 1) % this.names.length] });
    },
  };
  $("skin-select").addEventListener("change", (ev) => send({ type: "skin", name: ev.target.value }));

  const themePicker = {
    current: null,
    render(names, theme) {
      if (!theme) return;
      this.current = theme.name;
      const select = $("theme-select");
      select.textContent = "";
      const shown = withActive(names, theme.name);
      for (const name of shown) {
        const o = document.createElement("option");
        o.value = o.textContent = name;
        o.selected = name === theme.name;
        select.append(o);
      }
      $("theme-pick").hidden = shown.length < 2;
      if (theme.fallback_reason) notice(`theme: ${theme.fallback_reason}; using default`, "error");
      if (theme.note) notice(theme.note, "error");
    },
  };
  $("theme-select").addEventListener("change", (ev) => send({ type: "theme", name: ev.target.value }));
  addEventListener("keydown", (ev) => {
    if (ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target) || !skin.info) return;
    const hit = Object.entries(skin.info.options || {}).find(([, option]) => option.key === ev.code);
    if (hit) {
      ev.preventDefault();
      skinOptions.cycle(hit[0]);
    } else if (ev.code === skinPicker.key) {
      ev.preventDefault();
      skinPicker.cycle();
    }
  });

  function frame(t) {
    if (paneResized) { paneResized = false; resize(); }
    orb.level = orb.state === "listening" ? Math.min(1, ptt.micLevel * 6) : orb.state === "speaking" ? player.level() : 0;
    const dpr = devicePixelRatio;
    const f = { state: skin.shown(shownState()), level: orb.level, time: t, width: canvas.width / dpr, height: canvas.height / dpr, scale: orb.size, options: skin.values };
    try {
      paintFrame(ctx, { skin, dpr, frame: f });
    } catch (err) {
      skin.giveUp(`skin threw (${err.message})`);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  window.orbPage = { orb, send, player, ptt, panelsUi, widgetsUi };
  connect();
})();
