# agent-orb

A voice assistant with drop-in panels for Claude Code and Copilot CLI, on Windows and macOS. Hold to talk, hear
the agent answer in a local voice, ask it to show something and a panel slides in. With the built-in engines, speech
never leaves the machine: Moonshine for speech-to-text, Kokoro for the voice, both on the CPU. An engine you add
yourself may be a cloud service (see [Speech engines](#speech-engines)).

## What you get

- **Push to talk**: hold the button, or Space with the window focused, and speak; the reply is read aloud.
- **Local speech**: the built-in transcription and voice run on your machine; nothing is sent to a speech service. An engine you add yourself can change that.
- **Voice and speed**: pick the speech engine and its voice from the bar, and set the speed with a slider; each is saved per repo.
- **Mute**: the bar's voice toggle, `M`, or `orb voice off` silences replies without closing the orb.
- **Stop**: a stop button ends the speech, or the agent's turn, at once.
- **Talk while it works**: status, repeat and "what did I miss?" are answered at once; anything else waits for the turn to end.
- **Transcript**: every exchange, typed or spoken, with the agent's steps folded in (`T`).
- **Panels**: ask the agent to show something and a page slides in beside the orb, with tabs, explain buttons and live data from your own feeds. See [Panels](#panels).
- **Widgets**: small pages of your own floating over the orb, such as a clock or a usage meter. See [Widgets](#widgets).
- **Skins and themes**: change how the orb looks (`B` cycles skins) and how everything around it is styled. See [Skins](#skins) and [Themes](#themes).
- **Wake word**: optional hands-free start, which can answer your enrolled voice only. See [Wake word](#wake-word).
- **Your own engines**: plug in any speech-to-text or voice engine as a folder. See [Speech engines](#speech-engines).
- **Both agents**: Claude Code and GitHub Copilot CLI, on Windows and macOS.

Spec: `specs/orb/spec.md`. Plan: `specs/orb/plan.md`. Both approved 2026-10-05; the build notes at the end of
the spec list where the build departed from it.

## Install

Needs `uv`, `node` (24+), and Chrome or Edge. Python dependencies install themselves on first `orb open`; the
speech models (about 350 MB) download once into `~/.cache/orb/`.

**Claude Code** (2.1.275 or later installs in one step):

```
/plugin install agent-orb --marketplace NerdoBaggins/agent-orb
```

On an older Claude Code, add the marketplace first: `/plugin marketplace add NerdoBaggins/agent-orb`, then
`/plugin install agent-orb@nerdobaggins`. Start Claude Code with the channel flag:

```
claude --dangerously-load-development-channels plugin:agent-orb@nerdobaggins
```

The flag is what lets the orb push requests into the session (channels are a research preview); Claude Code asks you
to confirm it at every start.

**Copilot CLI**: install from the same marketplace, then start Copilot as a UI server so the orb can attach:

```
copilot plugin marketplace add NerdoBaggins/agent-orb
copilot plugin install agent-orb@nerdobaggins
copilot --yolo --ui-server --port 4321 --continue
```

**From a local checkout** (developing the orb), load the plugin for one session instead of installing it:

```
claude --plugin-dir <path to agent-orb> --dangerously-load-development-channels plugin:agent-orb@inline
copilot --plugin-dir <path to agent-orb> --yolo --ui-server --port 4321 --continue
```

Then, in either agent: *"open the orb"*. The skill runs `orb open`, the window appears, hold the button (or Space
while the window has focus) and talk. The bar is a faint pill in the top-right corner (of the orb's side while panels share the window): a dot in the orb's state
colour, the wake word, and anything that needs attention. Hover it to open the rest — the session the orb is
attached to (Claude Code's `/rename` name, Copilot's session name, following a rename within a few seconds), the
transcript toggle (also `T`), the voice toggle, the voice-speed slider, the orb-size slider (marked at normal, not saved), the speech menu (every installed tts engine; switching
loads it on the spot and is saved for this repo), the voice menu (the current engine's voices, when it offers them;
each engine keeps its own choice) and the skin menu; the transcript is the orb's own exchanges, hidden until asked for.
With panels open, the orb takes a third of the window. Double-click the orb and it takes the whole window;
double-click a panel's tab and that panel does, with the orb out of sight and a dot in the bar carrying its state;
either again, or `Escape`, restores the split. `panels_key` (`P`) hides the panels and brings them back as they were. The divider between them drags, and dropped near an edge gives that side the window. With no panels
the orb has the whole window.

The orb follows the session, not just its own requests. A turn typed in the terminal shows as working, with the step
it is on, and the transcript lists everything: what was typed (here or there), what the agent said, the steps between
them folded into one line. Every reply is spoken, whoever asked: one typed in the terminal, and the one the agent gives
when a background task it started finishes. Replies that finish while the window is unfocused also count as new,
spoken or not: a badge in the bar and the title, cleared when you look at the window, and "what did I miss?" reads
them aloud. While the session is busy the
orb answers repeat, status and what-did-I-miss itself, and holds anything else to send as your next request when the
turn ends. Closing the window ends the orb: the server exits 20 seconds after its last window goes, and `orb stop` ends it at once. The window keeps the outcome of your last try (what it heard, or why it could not listen) until the next one.

## Commands

The skill runs these as `uv run --project <agent-orb> orb ...`:

| Command | What |
|---|---|
| `orb open [--agent claude\|copilot]` | start this session's orb server and window (one per agent per repo), on the same port every time |
| `orb stop [--agent claude\|copilot]` | stop this repo's orb and close its window at once |
| `orb panel show <file.html\|name\|transcript> [--title] [--name]` | show a panel |
| `orb panel close <name>` | close one |
| `orb panels list` | saved panels |
| `orb widgets list` | installed widgets, with their scope, which are on, and which names are also panels |
| `orb widget on\|off <name>` | show or hide one of your widgets over the orb, saved for this repo; with no orb open, sets it for the next one |
| `orb voice on\|off\|toggle [--agent claude\|copilot]` | mute or unmute the orb's speech without closing it; with no orb open, sets it for the next one |
| `orb voice enroll [--agent claude\|copilot]` | enroll your voiceprint in the open orb (five phrases, hold to read) |
| `orb log [-n N] [--agent claude\|copilot]` | the newest lines of this repo's orb log |
| `orb doctor [--no-engines]` | read-only checks: tools, browser, config, skin, agent links, engines |

## Config

Four layers, weakest first; each overlays the one before it per key. Never put credentials in any of them (startup
refuses a key that looks like one; credentials come from the environment). `config.example.json` is the full set.

| # | File | Whose |
|---|---|---|
| 1 | built-in defaults | the plugin |
| 2 | `~/.config/orb/config.json` (Windows: `%USERPROFILE%\.config\orb\config.json`) | you, every repo |
| 3 | `<repo>/.orb/config.json`, optional | the repo's team; keys and `speak` only |
| 4 | `~/.config/orb/repos/<repo id>/config.json` | you, this repo only |

A repo's own file (layer 3) may set only `ptt_key`, `mute_key`, `skin_key`, `panels_key` and `speak`. Everything
else (engines and their settings, the skin, the theme, the wake word, `copilot_port`, widgets, folders) is yours
alone, so cloning a repo cannot change what runs on your machine, where requests go or what shows over the orb; a
repo file that sets one is read without it and the orb says which key it ignored. A repo file that is not valid JSON,
gives an allowed key the wrong type, or holds a credential-named key still stops the orb from opening until it is fixed.

What the orb writes from its own window (the speed slider, the skin and its options, the voice toggle) goes to layer 4, so
a choice made in one repo stays there. The `voice` button in the bar (hover it) or `mute_key` mutes the orb: speech
stops at once, nothing is rendered while muted, and a reply that arrives muted is not read on unmute.

| Key | Default |
|---|---|
| `ptt_key` | `Space` (a `KeyboardEvent.code`) |
| `skin` | `pulse` |
| `skin_options` | `{}` — `{<skin>: {<option>: <value>}}` |
| `skin_sizes` | `{}` — `{<skin>: <scale, 0.5 to 2>}`, set from the size slider |
| `skin_key` | `KeyB` — cycles through the installed skins; the bar's `skin` menu picks one |
| `theme` | `default` — styles the controls and every panel; the bar's `theme` menu picks one |
| `speak` | `true` |
| `mute_key` | `KeyM` |
| `panels_key` | `KeyP` — hides the open panels, and brings them back as they were |
| `stt` | `{"engine": "moonshine", "moonshine": {}}` |
| `tts` | `{"engine": "kokoro", "speed": 1.18, "kokoro": {"voice": {"af_heart": 0.55, "bf_emma": 0.45}, "lang": "en-gb"}}` — `speed` is one number or per agent, `{"claude": 1.1, "copilot": 1.3}` |
| `wake` | absent (push-to-talk only); see Wake word |
| `copilot_port` | `4321` |
| `panel_dirs` | `[]` — folders for saved panels, absolute or `~/…`; only your own two files may set it |
| `theme_dirs` | `[]` — folders for themes, absolute or `~/…`, searched first; only your own two files may set it |
| `skin_dirs` | `[]` — folders for skins, absolute or `~/…`, searched first; only your own two files may set it |
| `widget_dirs` | `[]` — folders for widgets, absolute or `~/…`; only your own two files may set it |
| `widgets` | `{}` — per widget, `{"on": true, "x": 0.02, "y": 0.04, "width": 180, "height": 44}`: whether it shows and where, with `pane_width` and `pane_height` (the orb pane's size when it was placed, written by the orb) so the widget's size scales with the pane afterwards; merges per widget; only your own files may set it |

An engine's settings sit in a block under its own name inside `stt` or `tts`, so a layer that says only
`"tts": {"engine": "voxtral"}` gets the `voxtral` block from the layers beneath it and nothing from `kokoro`'s.
The old flat shape (`tts.voice`, `tts.lang`) is still read, as the `kokoro` block, with a warning naming the file.

Runtime state (ports, tokens, the channel secret) lives in `~/.config/orb/state/<repo id>/`, never in the repo.

The orb window opens in a browser profile of its own, `~/.config/orb/browser-profile/`
(`%USERPROFILE%\.config\orb\browser-profile\` on Windows). The browser asks for the microphone once per repo and
agent, since each has its own port.

The server logs what happens — windows, states, what was heard, wake-word firings and follow-ups, requests and
replies, speech and how long it took to render, engine, voice and skin switches, errors from the window — to
`server.<agent>.log` in that folder. It is always on and rotates at 1 MB through four older files, so it holds
about 5 MB and drops the oldest. Once a minute it records how well the wake word kept up, and warns when it was
starved (fewer than four fifths of the frames the mic sent: an overloaded machine or a throttled window).
`orb log [-n N]` prints the newest lines. `server.<agent>.out` holds the process's own output for the current run.

## Panels

A panel is any HTML page the agent writes. It gets a default stylesheet and `orb.send(text, {speak})`, which sends
a request into the session from a click and resolves with the reply. A saved panel reloads itself when its files
change. A panel carries no code: to keep one current, your own feed in `~/.config/orb/feeds/<panel name>/` (or `~/.config/orb/repos/<repo id>/feeds/` for one repo) names a
command and an interval, and the orb runs it while the panel is open and hands its JSON to the page as `orb.data`.
A panel that changes something (stars a story, signs in again) calls `orb.action(name, args)`: your own
`actions.json` in `~/.config/orb/actions/<panel name>/` (or the repo's folder beside its feeds) maps each name to
`{"command": [...], "click": true, "seconds": 20}`. The orb runs the command in that folder with `args` as JSON on
stdin and hands back what it prints as JSON; a non-zero exit rejects with its last stderr line. An action needs a
click unless it says `"click": false`, and the orb checks the click itself rather than taking the panel's word.
Saved panels live in `~/.config/orb/repos/<repo id>/panels/<name>/` (yours, for that repo only), any folder in your `panel_dirs` (yours, kept wherever you choose, such as a synced notes folder) or `~/.config/orb/panels/<name>/` (yours, for every repo) with an `index.html` and a `panel.json` (`title`, `description`). The skill
(`skills/orb/SKILL.md`) tells the agent all of this.

Panels and widgets come only from your own folders. A repo's own `<repo>/.orb/panels/` or `<repo>/.orb/widgets/` is
never loaded: a page you did not write cannot appear beside yours, take one of your names or be handed your feeds,
and `orb panels list` and `orb widgets list` say so when a repo ships one.

## Widgets

A widget is a small page of your own floating over the orb, beside the microphone: a meter, a clock, a count. It is
a folder with an `index.html` and a `widget.json` (`version`, `description`, and optionally `size` as
`{"width": 160, "height": 48}` and `corner`, one of `top-left`, `top-right`, `bottom-left`, `bottom-right`), found
in the same four places as saved panels, `widgets/` for `panels/` (`widget_dirs` for `panel_dirs`). An installed widget is off until you tick it on in the bar's `widgets` menu (or
`orb widget on <name>`); then it appears at its corner, drags by its top edge, resizes by its bottom-right grip, and
the orb remembers where you put it for this repo. It hides with the orb when a panel takes the whole window.

A widget's page gets what a panel's gets: the theme, `orb.send`, `orb.speak`, `orb.action`, `orb.state` and
`orb.data`. Its data comes from your own feed under the same name a panel's would, `~/.config/orb/feeds/<name>/`,
so one feed serves a name as a widget and as a panel. A widget brings only a page; the orb runs no code from its
folder. Every feed and action runs with `ORB_REPO`, `ORB_AGENT` (`claude` or `copilot`) and, while the orb knows it,
`ORB_SESSION` in its environment, so a feed about the attached session reads that instead of guessing. Such a feed
writes its JSON to the path in `ORB_OUTPUT` rather than `data.json`, so each orb on a repo gets its own reading; a
feed whose answer is the same for every orb keeps writing `data.json`, which they share.

## Skins

`skins/README.md`. Drop a folder in any folder in your `skin_dirs` or in `~/.config/orb/skins/<name>/`; it appears in the bar's `skin` menu, and
`skin_key` cycles through every installed skin. The choice is saved for this repo. `pulse` is the default.

## Themes

A skin draws the orb; a theme styles everything around it. A theme is a folder in any folder in your `theme_dirs`
(kept wherever you choose, such as a synced folder) or in `~/.config/orb/themes/<name>/`:

```
theme.json   {"name", "version", "description", "tokens": {"accent": "#00e5ff", ...}, "skin": "<name>",
              "tts": {"engine": "<engine>", "<engine>": {"voice": "<voice id>"}}}
orb.css      optional: the orb's controls (the corner pill, mic, stop button, caption, panel tabs)
panels.css   optional: every panel
```

Fonts and images beside them are served too. Every color and font on both pages is an `--orb-*` token: `stage`
(behind the orb), `bg` (panels), `surface`, `fg`, `dim`, `accent`, `line`, `edge`, `ok`, `warn`, `err`, `font`,
`display` (headings; the body font unless set), `mono`. A theme's tokens are written after the defaults and its sheets after them, so a theme with only tokens
recolors everything; a panel's own styles still come last and win. A selector that names something the orb no longer
has styles nothing. The bar's `theme` menu switches it for this repo, and when the theme names a skin that is
installed, the skin switches with it, once: picking another skin afterwards keeps the theme. A theme's `tts` works
the same way: the engine and voice it names switch with it when that engine is installed and loads, and are saved for
this repo; a voice picked afterwards stays. When they cannot be used the current voice keeps speaking and the orb
says why, and `orb doctor` flags a theme naming an engine that is not installed. `default` is the original look,
names `pulse` and speaks in the built-in `kokoro`, with whichever voice was last picked for it.

What an `orb.css` can name: `.bar` (the corner pill) with `.bar-title`, `.bar-btn`, `.bar-options` and `#bar-state`
(its state dot); `#mic`, `#stop`, `#caption`, `#hint` and `#notice` under the orb; `#panels`, `#tabs`, `.tab` and
`.tab.active`, and `#divider` beside it; `#tasks-list`; and `body[data-state="listening"]` and the other states.

## Speech engines

An engine is a folder, run as its own process:

```
~/.config/voice-engines/<kind>/<name>/        yours, shared with any other tool on this machine
<agent-orb>/engines/<kind>/<name>/            built in: stt/moonshine, tts/kokoro, vad/silero, voiceprint/resemblyzer
  engine.json   { "name", "version", "kind", "run": ["uv", "run", "--script", "engine.py"], "env": ["SOME_API_KEY"], "max_seconds": 20 }
  engine.py     brings its own dependencies (inline script metadata); agent-orb installs nothing for it
```

agent-orb ships only the local engines above. An engine you add runs with your permissions and does whatever it is
written to do: one that wraps a cloud service sends your recorded audio (speech-to-text) or every reply's text (voice)
to that service, under its terms. Check what an engine calls before you install it; an `env` entry naming an API key
is the usual sign that it talks to one.

An engine's, skin's or theme's folder name is lowercase letters, digits, `-` and `_` (up to 64); a folder named any
other way is neither listed nor loaded.

The orb starts the engine `stt.engine` / `tts.engine` names, keeps it warm, and talks JSON lines: `{"op": "load",
"settings": {…}}` once, then `{"op": "transcribe", "pcm": <base64 float32 16 kHz>}` → `{"text": …}` or
`{"op": "render", "text": …, "speed": …}` → `{"rate": …, "pcm": …}`, `{"op": "frame", "pcm": …}` → `{"score": …}` for a
`wake` or `vad` engine, `{"op": "embed", "pcm": …}` → `{"vector": […]}` for a `voiceprint` one; a reply carrying `error`
is a failure. A `tts` engine that offers voices also answers `{"op": "voices"}` → `{"voices": [{"id", "label"}],
"current": id}` and `{"op": "voice", "id": …}`; one that does not simply has no voice menu. `env`
names the variables the engine needs (credentials never go in config); `max_seconds` is the longest clip an STT
engine accepts, and the orb cuts longer ones at quiet points. An engine that is missing, fails to load or dies gives
way to the built-in default and the orb says why. The user's folder is searched first; a repo can neither name nor
provide one.

## Wake word

Off unless a config layer has a `wake` section; then the mic stays open while the orb is up (the browser's
indicator shows it), and the orb listens for the phrase while idle:

```json
"wake": { "engine": "openwakeword", "model": "hey_jarvis", "threshold": 0.5, "vad": "silero",
          "silence_s": 1.5, "prespeech_s": 4, "follow_up_s": 8, "voice_auth": false, "voice_auth_threshold": 0.72 }
```

- `model` has no default: it names the wake model you installed, and the phrase the orb strips is read from it
  (`hey_jarvis` → "hey jarvis"). The rest default as shown.
- The wake engine is a `wake` engine folder the user installs (`~/.config/voice-engines/wake/<name>/`); agent-orb
  ships none, and the models openWakeWord publishes (`hey_jarvis` among them) are CC BY-NC-SA 4.0, so using one is
  the user's own licence call. `silero` (the VAD) and `resemblyzer` (voiceprints) are built in.
- Two frames at or over `threshold` fire it; the recording keeps the second before and runs until `silence_s` of
  quiet, or gives up after `prespeech_s` with no speech. The phrase is stripped from the transcript. Frames are
  dropped in every state but idle, so the orb's own reply cannot wake it. Push-to-talk keeps working and takes
  precedence.
- For `follow_up_s` after the reply to something asked by the wake word, speech needs no wake word. A reply to the
  button, or to a turn typed in the terminal, opens no follow-up.
- `voice_auth: true` answers only the enrolled voice. Enroll with the orb open: `orb voice enroll` shows five
  phrases to read while holding the button; the voiceprint goes to `~/.config/orb/voiceprint/<engine>.json`,
  readable by you only, never in a repo. With it on and nothing enrolled, the wake word stays off and the orb says so.
- A wake, VAD or voiceprint engine that fails, or bad settings, turn the wake word off for the session with the
  reason shown; push-to-talk is unaffected.

## Layout

| Path | What |
|---|---|
| `.claude-plugin/`, `.mcp.json`, `hooks/`, `skills/` | the plugin, read unchanged by both agents |
| `orb/` | the Python package: CLI, server, config, state, the engine runner, Stop hook, interrupt adapters, doctor (tests beside the code) |
| `channel/` | the Claude Code channel MCP server (Node, no dependencies) |
| `copilot/` | the Copilot SDK helper (Node, no dependencies) |
| `web/` | the orb page, the push-to-talk worklet, the panel API and stylesheet |
| `engines/` | built-in speech engines, one folder each; `moonshine` and `kokoro` are the defaults (the bar's hearing and speech menus switch between installed engines), `fake` serves the tests |
| `skins/` | orb skins; `pulse` is the default |
| `themes/` | orb themes; `default` is the original look |

## Developing

```
uv sync --group dev
uv run pytest            # the channel and panel-API tests need node
```

Every plugin test runs in a throwaway repo outside this one with the plugin loaded by `--plugin-dir`; the plugin is
never installed into agent-orb itself.

## License and credits

agent-orb is MIT-licensed (`LICENSE`). The speech models are not part of this repo; each engine downloads its own on
first use, under its own license:

| Engine | Model | Through | License |
|---|---|---|---|
| `kokoro` (speech) | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT) | Apache 2.0 |
| `moonshine` (hearing) | [Moonshine](https://github.com/moonshine-ai/moonshine), English | useful-moonshine-onnx | MIT |
| `silero` (voice activity) | [Silero VAD](https://github.com/snakers4/silero-vad) | [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache 2.0) | MIT |
| `resemblyzer` (voiceprint) | [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) | itself | Apache 2.0 |
