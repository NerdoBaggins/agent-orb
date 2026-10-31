# Spec: Orb, a voice assistant with drop-in panels for coding agents

Author: Alberto Gonda. Status: approved (2026-10-05). Intent: `specs/orb/intent.md` (accepted 2026-10-05). Amended by `specs/customization/spec.md` (approved 2026-10-16).

## Goal

A plugin, `agent-orb`, that a user adds to Claude Code or Copilot CLI in any repo, on Windows or macOS. It opens a
local window holding an orb. The user holds the mic button, or a key while the window has focus, and speaks. The
agent does what was asked and answers in a local voice. When the user asks to see something, the agent writes a panel and the orb shows it on the right, shrinking to make room.

## Non-goals

- Cloud speech in either direction.
- A wake word, an always-on mic, or a global hotkey.
- Linux. Windows and macOS only.
- Several agent sessions behind one orb. Each session gets its own orb.

## Audience

The author first, on his own machines. Everything is per-user and documented from the start so colleagues can adopt
it next, but setup docs for others, a release, and the espeak-ng licence check are not part of version 1.

## Parts

1. **Plugin** (packaging, below): the skill, the `Stop` hook, the channel MCP server, and the scripts.
2. **Orb server** (Python, run with `uv`). Serves the page and panels, holds one WebSocket to the page, runs
   speech-to-text and text-to-speech, shows panels, relays requests to the agent.
3. **Orb page**, opened as a Chrome `--app` window (Edge `--app` where Chrome is absent).
4. **Agent link**, one per agent (below). The only Node code: the channel server and the Copilot helper.
5. **Panels**: any HTML the agent writes, one-off or saved. One built-in: the transcript.
6. **Orb skins**: drop-in folders. Built-in default: `pulse`.

Node is allowed for the two agent bridges only, because each must speak a JavaScript-only API. Everything else is
Python run with `uv`, with dependencies declared inline (`# /// script`) or in the plugin's `pyproject.toml`.

## Packaging

One repo, one plugin, serving both agents. A plugin bundles the skill, the hook and the MCP server together, which is
what the orb needs. Claude Code installs it with `claude plugin install`; Copilot CLI with `copilot plugin install`.

- One manifest folder serves both agents (test 0.1): `.claude-plugin/plugin.json`, `skills/`, `hooks/hooks.json` in
  Claude Code's format, and `.mcp.json`. Copilot reads all of it unchanged.
- Nothing is copied or written into an adopting repo. A repo may add `.orb/config.json`, limited to the key bindings
  and `speak`; it is optional and written by the repo's owner, never by the orb.
- Runtime state (ports, secrets, the pending-request list) lives in a per-user state folder,
  `~/.config/orb/state/<repo id>/`, where `<repo id>` is derived from the repo root's absolute path. The folder and
  its secret files are readable by the user only.
- Paths inside the plugin are resolved from the plugin's own root: `${CLAUDE_PLUGIN_ROOT}` in hook and MCP config,
  which both agents expand, and the script's own location in Python. Nothing hardcodes a checkout location, `/tmp` or
  a drive letter.
- The skill reaches the `orb` command through its own base directory. Both agents tell the model, when the skill
  loads, "Base directory for this skill: <path>"; the skill text says the command is two folders above it. Skill text
  never uses `${CLAUDE_PLUGIN_ROOT}`, which Copilot leaves unexpanded. Nothing is put on PATH.
- The channel and the Copilot helper have no npm dependencies, so there is no setup step: the four MCP messages the
  channel needs are spoken raw over stdio (build note 1).

## Launch

The agent runs the skill's script: `orb open [--agent claude|copilot]`.

- The script starts the orb server for this session if one is not running, then opens the window. It prints the URL
  and the attach state, and returns.
- **One orb per session.** Each session's orb has its own server and port. The port is stable per repo and agent
  (picked once from a free port and kept in the state folder), so Chrome remembers the mic permission for that
  origin. A Claude Code session and a Copilot session in the same repo therefore each get their own orb. The header
  names the repo and the attached session.
- **Limit:** one orb per agent per repo. A second Claude Code session in the same repo finds the first one's orb and
  channel already running; `orb open` says so, names the session it is attached to, and does not start another.
- **Per-launch token.** The server binds `127.0.0.1` only. `orb open` creates a random token and hands it to the
  server on standard input, so it is on no process command line. The window is opened on a file in the state folder,
  `open.<agent>.html`, readable by the user alone, which sends it on to the page at `/?token=…`; the page carries the
  token from then on, and `orb open` prints the address without it. The window
  runs in the orb's own browser profile (`~/.config/orb/browser-profile/`, user-only), so its history and cookies
  never mix with the user's everyday browsing. Panels never see the token (they go through `orb.js`). Non-browser
  clients (`orb panel ...`, the `Stop` hook) read it from `server.json` in the state folder, which also holds the
  port and the server's pid. Every HTTP request and the WebSocket must present it, except a panel's or widget's
  files (under their own path secret) and the orb page's own `/skin/` and `/theme/` files fetched
  same-origin; any request whose `Origin` header is present and is not the orb's own origin is rejected. Failures
  get 403.
- **Channel secret.** The channel starts with the Claude Code session, before any orb exists, so it creates its own
  random secret at start and writes it, with its port, to `channel.json` in the state folder. The orb server reads it
  from there. `/prompt` requires that secret, requires `Content-Type: application/json` (which forces a browser
  preflight the channel never answers), and rejects any request carrying an `Origin` header. No website open in the
  browser can drive the orb or push a prompt into the agent.
- The window opens with `--autoplay-policy=no-user-gesture-required` so the first reply always plays.
- Opening the window is a platform adapter: Windows `chrome.exe --app=<url>`, macOS
  `open -na "Google Chrome" --args --app=<url>`, Edge as fallback on both. A missing browser is reported with what was
  looked for.

## Layout

- With no panel open, the orb fills the window.
- Opening a panel slides it in from the right at about two thirds of the width, and the orb animates into the rest.
  Closing the last panel reverses this. The orb keeps a usable minimum size.
- Several open panels become tabs. Opening one already open brings it to the front.
- A panel closes by its close button or by voice ("close that panel").
- The header shows the repo, the agent link state, and the speech engines in use.

## Talking (push-to-talk)

1. The user holds the mic button, or holds the push-to-talk key (config `ptt_key`, default Space) while the window has
   focus. The key is ignored while focus is in a text field.
2. The page records with `getUserMedia`, resamples to 16 kHz mono in an AudioWorklet, and streams frames over the
   WebSocket while held. The orb shows **listening**, driven by the mic level.
3. On release the server takes the clip. A clip under about 0.3 s is dropped as a tap; there is no upper limit, and a long clip is transcribed in pieces of at most 20 s cut at quiet points.
   The orb shows **transcribing**.
4. The speech-to-text engine returns text. An empty result shows
   "didn't catch that" and nothing is sent.
5. The text is added to the transcript and goes to the agent as a request with a request id. The orb shows
   **thinking**.
6. The reply is rendered and played (Speaking, below). The orb shows **speaking**, then **idle**.

### Stop and interrupt

While the orb is busy, the mic button becomes **Stop**; the push-to-talk key does the same.

- While **speaking**: playback ends at once and the orb goes idle.
- While **thinking**: speech ends and the agent's current turn is aborted. The orb server marks the request
  interrupted in the transcript when it sends the abort, and drops it from the pending list; Claude Code's `Stop` hook
  does not fire on an interrupted turn (test 0.2), so no reply is awaited.
  - Copilot: the SDK's `session.abort()`, sent only while the orb's own request is the turn running. An abort ends
    the running turn whoever it belongs to and kills its attached background shells; after the turn has ended it
    leaves them alone (measured 2026-10-09). So Stop never aborts a turn Copilot started on its own, nor one the
    orb's message is queued behind.
  - Stop always lets the orb go: where nothing may be aborted it drops the wait, goes idle and says the agent keeps
    going. A request cancelled in Copilot's own window ends the wait the same way. Still unmeasured: whether a send
    during another request's running turn arrives as `queued` or `steering`; both are followed.
  - Claude Code on Windows: an Escape key event written to the session's console input (`AttachConsole` +
    `WriteConsoleInput`), without focusing its window. Proven under Windows Terminal and conhost (test 0.2). One
    Escape only, sent only while a request is running, and only after confirming the process is this repo's session.
  - Claude Code on macOS: Escape sent to the session's terminal through AppleScript; iTerm and Terminal are two
    adapters, chosen by the session's parent process. iTerm writes the byte into the session's pty and no window
    moves; Terminal has no such call, so it keys the tab through System Events and takes focus. Proven on macOS 26
    (test 0.2).
  - Where no interrupt adapter works, Stop ends speech only and the orb says the agent is still working.
- **One request in flight; the rest wait their turn.** While the orb or the agent is busy (the orb's own request, or a
  turn typed in the agent's terminal), holding the mic still listens. Asking to repeat the last reply, what is running,
  or what you missed is answered by the orb itself. Anything else said, and a panel's `orb.send`, is held and sent once
  the turn ends, in the order it was made: speech held in a row goes as one request, and each panel request goes as
  its own, its reply resolving it. Nothing is sent while no window is open; a window that opens sends what waits.
  Held requests are dropped, with a notice (a panel's send is rejected), when Stop ends the turn they wait on, when
  the agent goes away, or when the orb finds it in another session. Stop that only ends speech keeps them.
  Not sent into the running turn: Claude Code reads a channel message that lands mid-turn beside the turn's tool
  results and declines it as not the user's (measured 2026-10-26, twice: the turn ended with "that message didn't
  come from you"), and the orb treats both agents alike.

## Speaking

The server renders speech; the page plays it.

- The reply is split into sentences and rendered one at a time, rendering ahead while the page plays, so speech
  starts within seconds. A whole paragraph is rendered in one batch, and on a Windows desktop a 57-word paragraph took
  5.2 s to first audio. One sentence at a time took 0.5 to 2.3 s (test 0.3).
- Rendered PCM goes to the page over the WebSocket. The page plays it with Web Audio and measures the level that
  drives the orb. One playback path on both OSes; Stop drops the queue instantly.
- The reply's markdown is read before the split (`speakable`, on markdown-it-py with GitHub tables): marks go, an
  image is read by its alt text, a table's rows are read as lists of cells, and a code block is named ("there's a code block here") rather
  than read. What counts as markdown is the CommonMark spec's call, so `__init__.py` outside backticks is bold.
- **Every reply is spoken while the orb is open** (2026-10-13), whoever asked: a turn typed in the
  terminal, and a turn the agent started itself when a background task finished. The orb's own requests are spoken
  when their reply is delivered; the session watcher speaks every other reply that ends a turn, read from the
  transcript or event log. It speaks one at a time, oldest first, only while the orb is idle and a window is
  connected, so it never talks over listening, a request or other speech. Replies already in the session when the
  orb first reads it, or attaches to another session, are not spoken. A spoken reply still counts as missed until
  the window is looked at, since the user may have been away from the desk. On
  Copilot a reply is the orb's when it carries an id the orb's message is answered under; once Stop lets go of a
  request without aborting it, its later reply is spoken like any other.

## Swappable speech engines

Each engine sits behind a small interface and is chosen by name in config. Trying another engine is one new module
plus one config value.

- **Speech-to-text:** `load(config)` once, then `transcribe(samples_16k_float32) -> str`. The engine owns its own
  silence trimming and clean-up.
- **Text-to-speech:** `load(config)` once, then `render(text) -> (samples, sample_rate)`. Queueing, playback,
  Stop and levels live outside the engine.
- Engines live in `engines/stt/<name>.py` and `engines/tts/<name>.py`. Each declares its own Python dependencies, so
  an engine that is not selected is never installed or loaded.
- An unknown engine, or one that fails to load, stops startup with a message naming the engine and the error. No
  silent fallback.
- Models load once at server start and stay warm. Model files download once to a user cache and run offline after.

**Default speech-to-text: Moonshine.** `moonshine/base` through `useful-moonshine-onnx`, CPU, offline. Clean-up:
trim leading and trailing silence, normalize gain, and collapse a stuck repetition loop in the output.

**Default text-to-speech: Kokoro.** Kokoro-82M through `kokoro-onnx`, original model (int8 is about 2x slower).
- Default voice: 55% `af_heart` + 45% `bf_emma`, `lang="en-gb"`, speed 1.18.
- espeak-ng ignores a long data path on Windows: copy `espeak-ng-data` to a short real path (not a link) and pass
  `EspeakConfig(data_path=...)`.

## Agent link

`orb open` takes `--agent claude` or `--agent copilot`; the skill text tells each agent which to pass. With no flag
the server probes both; if exactly one answers it uses it, if both answer it asks, if neither it says so. It never
guesses. The header shows `Attached: Claude Code`, `Attached: Copilot (<session id>)`, or `Not attached` with the fix.
Mic and ask buttons stay disabled until the link is verified.

### Claude Code

- **Delivery.** A Node channel MCP server, `orb-channel`, shipped in the plugin. Claude Code names it
  `plugin:agent-orb:orb-channel`, and that qualified name is what the transcript and the `Stop` gate see. It declares
  the `claude/channel` experimental capability, listens on `127.0.0.1:<channel port>/prompt`, and pushes each request
  as a `notifications/claude/channel` message. The channel port is per repo, kept in `channel.json` in the state folder
  (Launch, above), so two repos' sessions never cross. Requests without the channel secret are rejected (Launch).
- **Bound check.** `/health`, which needs the channel secret, reports `ok` only after the MCP initialize handshake; `/prompt`
  returns 503 before that, so an orphan channel never swallows a request. The channel exits when its stdin closes.
  If its port is taken, it keeps MCP alive and retries the bind; it never takes the port from another process.
- **Launch flag.** Channels push only when the session was started with
  `--dangerously-load-development-channels plugin:agent-orb@<marketplace>` (research preview; `@inline` for a plugin
  loaded with `--plugin-dir`). The flag works only in an interactive session, and Claude Code asks the user to confirm
  it at every start. If the channel is not bound, the skill and the orb print the exact restart command. No launcher
  script.
- **Not under Copilot.** Copilot reads the plugin's `.mcp.json` too, and starts its servers wherever the account's
  MCP allowlist permits them (test 0.1 ran on an account whose allowlist blocked it). The channel server exits at once
  when `COPILOT_CLI=1`, so it never binds a port or writes `channel.json` for a Copilot session.
- **Trust and answer style.** Claude Code wraps channel messages as untrusted. The channel server's MCP
  `instructions` (always in the session's context, unlike skill text, which loads on demand) tell the agent that
  `orb-channel` messages are the user's own voice requests and clicks from the local orb, and how to answer: do what
  was asked, then reply in plain spoken language, no markdown, names and keys read naturally, no preamble. With that
  text alone, ten varied requests got ten plain spoken replies (test 0.4). The skill repeats the same text for
  reference.
- **Slash commands are text.** A request is always delivered as text, never run as a command. Through the channel,
  a built-in such as `/compact` is answered ("I can't run that"), while a skill's slash name is recognised by the
  model, which invokes the skill itself (test 0.4). The orb does not type into the agent's console to run commands.
- **Return.** The plugin's `Stop` hook (Python, `uv`) reads the reply from the transcript and posts it to the orb
  server. It acts only when both hold:
  1. the last real user turn came through this channel:
     `origin: {"kind": "channel", "server": "plugin:agent-orb:orb-channel"}` on a `type: "user"` entry, or on an
     `attachment` of type `queued_command` when it arrived mid-turn. Tool results and skill injections open no
     turn. A background task's notification opens a turn of its own when it arrives after the last turn
     ended (Claude Code writes a `user` entry with `origin.kind: "task-notification"`), and that turn is not the
     orb's; one that lands mid-turn (a `queued_command` attachment) joins the running turn;
  2. that turn carries a request id the orb server sent to Claude Code and still has pending.

  Every other turn in every session is left to the session watcher (Speaking). The hook exits 0 and does nothing when no orb server is running,
  and treats a missing or unreadable transcript as a turn that is not the orb's. It takes the repo from
  `CLAUDE_PROJECT_DIR`, the folder the session started in, and from the `cwd` in its stdin payload only when that is
  unset; never from its working directory, which under Copilot is the plugin folder.

### Copilot CLI

- The user starts `copilot --experimental` in the repo: Copilot loads plugin extensions only in experimental mode
  (feature `EXTENSIONS`, availability experimental; `/experimental on` saves the mode in Copilot's settings). The plugin's Copilot extension
  (`extensions/orb/extension.mjs`) is started by Copilot with each session and joined to it over stdio
  (`joinSession`), so Copilot opens no listener of its own.
  `copilot --ui-server` is not used: its listener takes no credential.
- The extension opens the orb's door into the session on the terms the Claude Code channel keeps (`channel/door.mjs`,
  shared by both): loopback only, a fresh secret per start, no request with an `Origin` or without the secret. It
  records `{port, secret, pid, sessionId, repoRoot}` in the repo's state folder as `copilot-channel.json`. Routes:
  `GET /health` (session id), `POST /prompt`, `POST /abort`, `GET /tasks`.
- Copilot restarts the extension on `/clear`, under a new session and secret. The orb reads the record on every
  call; `/prompt` and `/abort` name the session the orb means, and the door refuses another with 409. A refused
  send is sent again to the new session; a refused abort lets go; a request still waiting ends once the record
  names another session.
- The reply is followed in the session's own event log (`~/.copilot/session-state/<id>/events.jsonl`). `send`
  returns the message id, and every reply to it carries that id as `originatingMessageId`; a message steered into
  a running request is answered under that request's id. The request is answered when a message for it asks for
  no tools and its turn ends. Not `sendAndWait`: it waits for `session.idle`, which an attached background shell
  holds off until the shell finishes, and Copilot's own `working` flag in `open-sessions-state.json` went stale
  on a live session.
- **Answer style.** A request is sent to Copilot behind a one-line tag, `[orb: your final message is read aloud, so put the whole
  answer in it]`, and nothing else (2026-10-13): the agent had ended a turn with a one-line `task_complete`
  summary and said nothing else, so nothing was heard. The tag stays short and starts `[orb`:
  Copilot loads no MCP instructions from the plugin, and repeating the answer-style text on every prompt cost tokens
  and cluttered its transcript. The tag is what a rule or skill of the user's own, per repo, keys on to say how the
  agent speaks and answers. The orb's feed and transcript show the request without the tag. The skill carries a
  default for sessions that have no rule of their own.
- **Slash commands are text here too.** A `/command` sent as a prompt reaches the model as text (it answered `/help`
  with its own help text). The SDK's `commands.enqueue` runs one as if typed; the door does not offer it yet.
- A Copilot without the plugin, or with experimental mode off, cannot be attached; the orb says so and prints the
  command, and `orb doctor` names experimental mode when Copilot's settings have it off.
- A question the agent asks in its own window cannot be answered from the orb; the orb says to answer it there.
- The `Stop` hook fires under Copilot too, at every turn end (test 0.1). Its transcript is Copilot's own
  `events.jsonl`, in which the gate finds no channel turn, so it stays silent.

## Panels

A panel is any HTML page the agent writes. The orb imposes no data format: the agent fetches whatever it needs with
its own tools and writes it into the HTML, or the page draws `orb.data` from a feed (Feeds). Anything a web page can draw, a panel can
show.

### Showing a panel

- `orb panel show <file.html> [--title <title>] [--name <name>]` shows a page; `orb panel close <name>` closes it.
  Showing a name already open replaces it. A `--title` over 200 characters is refused; a title taken from the folder
  name or `panel.json` is shortened to 200, as a name taken from the folder is shortened to 64.
- **One-off:** the agent writes the page as `index.html` in a folder of its own and shows it; the files beside it
  are served with it. A page under another name is served alone, and one sharing its folder with other files, or an
  `index.html` with other files beside it directly in the temp folder, `/tmp`, home or the root, is refused with
  how to fix it. A linked subfolder is not followed. The files are capped at 1000, each path at 255 characters,
  and 8 MB in all; a page over any cap is refused with which one.
- **Saved:** the agent saves the page to reuse it, in `~/.config/orb/repos/<repo id>/panels/<name>/` (the user's, for this repo only; `<repo id>` is the repo's name and a
  hash of its path, as for the state folder) any folder in the config key `panel_dirs` (the user's, kept wherever they choose) or
  `~/.config/orb/panels/<name>/` (the user's, for every repo). Where a name
  exists in more than one, the narrowest wins, in that order. `panel_dirs` is read from the user's two files only; in
  the repo's file it is ignored with a warning.
  A saved panel holds `index.html` (plus any files beside it) and a `panel.json` with a `title` and a `description`
  the agent reads to know when to reuse it. Reusing one means the agent fills it with fresh data and shows it again.
- `orb panels list` prints the saved panels with their descriptions. The skill tells the agent
  to check it before writing a new panel for the same job.
- **A saved panel is shown by name** and the server reads it from its own folder, never from a path in the request.
  While it is open, the server checks the folder every 2 s and reloads the panel when its files change and hold for
  two checks, whoever changed them. The reload leaves the tab where it is; showing the panel again brings it to the
  front. If the folder no longer makes a panel, the last page stays and a notice says why.
- **Feeds** (2026-10-13). A panel carries no code; the code that fetches its data is the user's. A feed is
  `~/.config/orb/repos/<repo id>/feeds/<panel name>/feed.json` (this repo only; looked for first) or
  `~/.config/orb/feeds/<panel name>/feed.json` (every repo), `{"command": [...], "every": <seconds, at least 30>, "output":
  "data.json"}`, beside whatever script it runs. While a panel of that name is open, the server runs the command in the
  feed's folder, without a shell, when its output is older than `every` and it has not been tried for as long, and
  hands the output (JSON, at most 1 MB) to the page as `orb.data` whenever it changes, whoever wrote it. The run is
  stopped, with everything it started, after 120 seconds or when the panel closes. A failed run, or
  output that is not JSON, keeps the last data, marks the panel's tab and says why once. The program is looked up on
  the feed's own `PATH`, and the orb's virtual environment is not passed on. Feeds are never read from a repo, so a
  panel from anywhere, shared or pulled, only ever displays.
  - This adds one panel protocol to the 2026-10-05 decision: `orb.data`. It adds no data commands: the orb runs only
    the user's own feeds, never anything a panel or a page names.

### `orb.send`

The orb injects one function into every panel:

- `orb.send(text, {speak = true})` sends `text` into the attached session as a request, marked with the page that sent it (`[sent by the orb panel "<name>", not spoken]`), otherwise like a spoken one,
  and resolves with the agent's reply. With `speak: true` the reply is also spoken; with `speak: false` it is only
  written in the session and returned to the panel.
- It works only during a real click or key press (the browser's user-activation flag) while focus is in that panel.
  A panel's own script can take focus, so in the seconds after a click elsewhere it can still send; the mark is what
  tells the agent the words are the page's.
- Blank text, empty or only whitespace, is never sent: the send is rejected with the reason.
- While the orb or the agent is busy it is held, so one request is in flight at a time: requests go in the order
  they were made, once the turn ends; a Stop that ends the turn drops what is waiting and rejects those sends.
- `orb.state` and `orb.on("state", fn)` let a panel follow idle, listening, thinking and speaking. A panel never
  disables its controls for it: navigation works whatever the orb is doing, and a send waits its turn.
- `orb.data`, `orb.dataAt` and `orb.on("data", fn)` give it what the user's feed for this panel last wrote (Feeds); a
  listener added after data arrived is called at once.

Example: an "Explain" button calls `orb.send("Explain this to me")`; a "Create PR" button calls
`orb.send("Create a PR for this branch", {speak: false})`.

### Sandbox and network

- Each panel runs in a sandboxed iframe on its own origin. It does not have the orb's token and cannot reach the orb
  server; `orb.send` (postMessage to the orb page) is its only way into the session.
- Panels can load from the internet, for example a charting library from a CDN. The skill recommends well-known CDNs
  (cdnjs, jsDelivr, unpkg).

### House style

The orb injects a small default stylesheet (colors, fonts, a speaker-button class) into every panel, and the skill
describes it, so agent-written panels look alike unless the agent chooses otherwise.

### Built-in panel: transcript

What the user said and what the agent answered, newest last, with interrupted requests marked. Each entry of the
session's feed, tool steps included, shows the local time it was written to the session log (`HH:MM:SS`), and a date
line comes before the first timed entry and wherever the day changes; an entry the log gives no time shows none. Each reply
has a speaker that reads it aloud in the orb's voice, with no agent turn, and stops that reading; while the orb reads
a reply to a turn typed in the terminal, that reply's speaker stops it too. The speakers are hidden when no
text-to-speech engine is loaded.

## Configuration

Layers, weakest first: the defaults, the user's file, the repo's file, the user's file for this repo
(`specs/customization/spec.md` has the full table).

- User: `~/.config/orb/config.json` (Windows: `%USERPROFILE%\.config\orb\config.json`).
- Repo, optional: `<repo>/.orb/config.json`. It sets only `ptt_key`, `mute_key`, `skin_key`, `panels_key` and
  `speak`; any other key there is ignored with a warning. Never holds secrets; startup rejects a key named like a
  credential.
- Credentials: environment only.

| Key | Meaning | Default |
|---|---|---|
| `ptt_key` | Push-to-talk key | `Space` |
| `stt` | `engine` and that engine's settings | `moonshine` |
| `tts` | `engine` and that engine's settings (voice blend, lang, speed) | `kokoro`, voice above |
| `skin` | Orb skin name | `pulse` |
| `panel_dirs` | Folders holding saved panels (absolute or `~/`) | `[]` |

The plugin ships `config.example.json`. Unknown keys are reported at startup.

## Orb skins

- A skin is a folder: `skin.json` (name, version) and `skin.js`, which draws one frame on a 2D canvas from
  `(ctx, {state, level, time, width, height})`. It must draw well at any size. No network, no external assets.
- Discovery: any folder in the config key `skin_dirs`, then `~/.config/orb/skins/<name>/`, then the plugin's built-ins. An unknown or broken skin falls back to
  `pulse` and the orb says why.
- **`pulse`**: concentric rings that breathe at idle, ripple inward while listening, orbit while thinking, and expand
  with the audio level while speaking. Flat 2D canvas, no shader.
- The plugin ships `skins/README.md` with the contract and a minimal example.

## Checks

**Unit tests** (pytest, run on Windows and macOS):
- config layering, validation, credential-key rejection, unknown-key reporting;
- engine selection and its failure messages, with a fake engine for each interface;
- saved-panel listing order and `panel.json` validation; `orb panel` command parsing;
- `orb.send`: rejected without user activation or with blank text, held while busy; `speak: false` returns the reply unspoken;
- skin manifest validation and fallback;
- agent selection (one, both, neither answering);
- token check on HTTP and WebSocket, and rejection of a foreign `Origin`;
- channel `/prompt` rejects: no secret, a wrong secret, a non-JSON content type, any `Origin` header;
- state folder: repo id derivation, and a second same-agent launch in one repo reported instead of started;
- the `Stop` hook gate: a channel turn from `plugin:agent-orb:orb-channel` with a pending Claude Code request id
  speaks; each of these stays silent: a typed turn, a channel turn without that id, a turn from another channel, a
  turn whose request went to Copilot, a turn with no pending request, no orb server running, a missing transcript, a
  Copilot `events.jsonl` transcript;
- the channel server exits when `COPILOT_CLI=1`.

**Behavioral checks** (`orb doctor`, read-only): Copilot port reachable; channel `/health` bound and
owned by this repo; each selected engine loads; browser found.

## Open questions (resolved by throwaway tests at the start of the plan)

1. Plugin manifests. Resolved by test 0.1: one folder, the qualified channel
   name and flag form above, the hook's working directory.
2. Interrupting a Claude Code turn. Resolved by test 0.2: Windows on 2026-10-06,
   macOS (iTerm2 and Terminal) on 2026-10-09 after two timing fixes to the adapter.
3. Moonshine and Kokoro speed. Windows resolved by test 0.3, on
   synthetic speech: both targets met, with replies rendered per sentence. MacBook Air (M1, 8 GB) run 2026-10-09:
   Moonshine met; Kokoro renders at or over playing time, so sentences pause between them and a long first
   sentence misses 3 s. Still open: real speech.
4. Whether the agent reliably follows the answer style for orb requests. Resolved by test 0.4: ten of ten plain spoken replies on each agent, with the text in the
   channel's MCP `instructions` for Claude Code and as a prompt prefix for Copilot.
5. Whether a slash command sent through the channel or Copilot's SDK runs as a command or arrives as text. Resolved
   by test 0.4: text on both. A skill name is still honoured, because the model invokes the skill. Typing into the
   agent's console (Windows `WriteConsoleInput`) and Copilot's `commands.enqueue` do run commands; neither is used.
6. How the skill reaches the `orb` command. Resolved: through the skill's base directory (Packaging), 2026-10-06.
7. Which hooks mark a turn open and closed. Resolved by the hooks spike, 2026-10-20:
   `UserPromptSubmit` opens one in both agents; `Stop`, `StopFailure` and `SessionEnd` close it. No hook fires on an
   interrupt or an SDK abort, so those still come from the transcript and the event log.

The channel spike in `channel/` predates this spec: it reads `.orb-channel-port` at the repo root and has no secret
or `Origin` check. It is reference only and is rewritten to this spec in the build.

## Build notes (2026-10-09)

Where the build departed from the text above, and why. Each is reversible.

1. **No npm dependency anywhere.** The channel speaks initialize, initialized, ping and tools/list raw over stdio,
   as the test 0.1 spike already did; the Copilot helper uses Copilot's own SDK. The "first run installs the
   channel's npm dependency" step and the skill's `setup` step are gone. One fewer failure mode on a fresh machine.
2. **`channel.json` also records `sessionPid`**, the process that spawned the channel, i.e. the Claude Code session
   for this repo. The interrupt adapter confirms that pid still names a Claude Code image and sends Escape there and
   nowhere else. This is the "confirm the process is this repo's session" check (Stop and interrupt).
3. **Panels are served under `/p/<random prefix>/<name>/`**, a path that is not the token and that the token
   check exempts. The sandboxed iframe's opaque origin means a panel's own requests to the server are refused
   (foreign Origin), so a panel still cannot reach the API; it only loads its own files.
4. **Engine interface:** `Engine(cache_dir)` then `load(settings)`, so each engine owns a cache folder
   (`~/.cache/orb/<kind>/<name>/`) for its model files. Kokoro renders one word at load, because the first render
   after load ran four times slower than the rest live and would have missed the 3 s target on the first reply.
5. **Engines load before the server binds**, so `orb open` waits (up to 300 s on the first run, which downloads
   the models) and the page never shows a half-ready orb. The CLI says what it is waiting for.
6. **The Stop hook runs through `uv run --no-project --python ">=3.11"`** (the 88 ms path from test 0.1) and
   imports `orb.gate` and `orb.state`, which are stdlib-only, rather than duplicating them.
7. **`orb.send` carries a page-side `ref`** through the request so the page can resolve the right panel's promise;
   the server echoes it on the reply, the interruption and any refusal. Not a panel-visible concept.
8. **Requests run as their own task on the server**, because Copilot's link blocks until the reply and the socket
   loop must keep reading for Stop to arrive. The request leaves the pending list before an abort goes out, since
   an aborted `sendAndWait` returns at once with an empty reply.
9. **`orb doctor` also checks `uv`, `node` and the skin**, beyond the four checks listed.
10. **`ptt_key` is a `KeyboardEvent.code`** (`Space`, `KeyV`), the one form that is layout-independent.
11. **The macOS interrupt adapter picks iTerm or Terminal from the session's ancestors** instead of trying iTerm
    then Terminal. Under iTerm it writes the Escape byte into the session's pty, so the orb window keeps focus;
    under Terminal it waits for the tab and app to actually be in front before keying Escape (2026-10-09, the Mac
    run of test 0.2: the key had been going to the orb window with the adapter reporting success, and the
    System Events route pulled focus to the terminal on every Stop).

Verified live on a Windows desktop in a throwaway repo, through the page's socket with recorded 16 kHz
clips in place of a microphone (this desktop has no mic): Claude Code voice loop 12.3 s end to end (0.2 s
transcription, 6.7 s agent, first audio 2.0 s before the warm-up fix); Copilot voice loop 8.5 s; Stop while
thinking cancelled a Claude Code turn in 0.1 s (`[Request interrupted by user]` in the transcript) and aborted a
Copilot turn; panels show, replace and close through the CLI; `orb doctor` all green with both agents up.

Verified on the MacBook Air (2026-10-09): push-to-talk with a real microphone, the spoken reply, a click
inside a panel in Chrome, Stop while thinking from the orb under iTerm2, the voice loop under Copilot, `orb
doctor` green with both agents up. Phase 7.3 is complete on macOS.

Seen on the Mac and not yet addressed: closing the orb window leaves its server running, and `orb open` then
reports "already open" without reopening a window — the only way back is to stop the server by pid. Two
`--app` windows for different ports open at whatever size Chrome last remembered for each origin, so two orbs
can differ in size; `--window-size` is not set.

Open (2026-10-09): an orb attached to a Copilot window started without `--plugin-dir`
accepts "show me a dashboard" and the agent, having no skill, writes an HTML file, opens it in its own browser
window and wires its speaker buttons to the browser's Web Speech API — the macOS system voice. The orb should
tell the user when the attached agent lacks the plugin, and the Copilot prompt prefix could say what a panel is.

Not verified: Terminal.app without the focus cost; a fresh Mac's TCC prompts on the first Stop. Also seen:
`copilot --continue` in a folder with no session of its own resumed the user's most recent conversation from
another folder (with a "may already be in use" warning).

## Decisions (2026-10-05)

Carried from the intent and handoff: orb with panels; own repo; Windows and macOS; Claude Code and Copilot CLI;
push-to-talk only; Stop interrupts; Moonshine and Kokoro, swappable; channel plus gated `Stop` hook for Claude Code;
Node helper for Copilot; Node for agent bridges only; replaceable skins with default `pulse`.

Confirmed at the spec stage: packaged as one plugin; audio played in the browser; one orb per session; author first,
then colleagues; a random per-launch token. Panels are any HTML the agent writes, one-off or saved, with `orb.send`
as the only orb API (click-only); no data commands, no `orb.speak`, no specific panels in
this spec; panels may load from the internet.
