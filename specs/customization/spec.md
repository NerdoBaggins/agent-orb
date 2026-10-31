# Spec: Make the orb yours — skins, voices, wake word, per repo

Author: Alberto Gonda. Status: approved (2026-10-16). Intent: `specs/customization/intent.md` (accepted 2026-10-16).
Amends `specs/orb/spec.md`; where the two disagree, this one wins, and each section below names what it replaces.

## Goal

A user can give each repo's orb its own look, voice, hearing and wake word, without touching the plugin or
committing anything. Anyone can add a skin or a speech engine by dropping in a folder. The defaults are unchanged:
`pulse`, Moonshine, Kokoro, push-to-talk.

## Non-goals

- Coordinating speech with other tools on the machine. If two things speak at once, both are heard.
- A repo shipping code that runs when the orb opens, or choosing which of the user's code runs. A repo's file sets
  only keys and `speak`; it names no skin, theme, engine or wake word, and provides none.
- Producing the `working` and `waiting` states. The skin contract makes room for them (Skins, States); emitting them
  is later work.
- A global hotkey. The wake word is the hands-free path.
- Barge-in by voice while the orb is speaking. Stop stays the button and the key.

## Configuration

Replaces `specs/orb/spec.md` Configuration.

### Layers

Weakest to strongest; each layer overlays the one before it.

| # | File | Whose | In git |
|---|---|---|---|
| 1 | built-in defaults | the plugin | — |
| 2 | `~/.config/orb/config.json` | the user, every repo | no |
| 3 | `<repo>/.orb/config.json` | the repo's team, optional: `ptt_key`, `mute_key`, `skin_key`, `panels_key`, `speak` only | yes |
| 4 | `~/.config/orb/repos/<repo id>/config.json` | the user, this repo only | no |

- `<repo id>` is the one panels and runtime state already use (`orb/state.py`: a readable name plus a hash of the
  absolute path). Moving or recloning a checkout starts its per-repo choices fresh, as it does for per-repo panels.
- Layer 3 never holds credentials (startup refuses a credential-named key, as today). Layers 2 and 4 are refused the
  same way: credentials come from the environment only.
- Anything the orb writes from its own window (speed, skin options, voice on/off) goes to layer 4. Today the speed
  slider writes layer 2; it moves to layer 4, so a pace set in one repo stays in that repo.

### Keys

| Key | Meaning | Default |
|---|---|---|
| `ptt_key` | push-to-talk key | `Space` |
| `skin` | skin name | `pulse` |
| `skin_options` | `{<skin name>: {<option>: <value>}}` | each option's own default |
| `skin_sizes` | `{<skin name>: <scale>}`, 0.5 to 2, written by the size slider | normal (1) |
| `speak` | whether replies are spoken | `true` |
| `mute_key` | key that toggles `speak` while the window has focus | `KeyM` |
| `stt` | `{"engine": <name>, <name>: {settings}, ...}` | `moonshine` |
| `tts` | `{"engine": <name>, "speed": <n or per agent>, <name>: {settings}, ...}` | `kokoro`, voice as today |
| `wake` | wake-word settings (Wake word); absent means push-to-talk only | absent |
| `panel_dirs` | folders holding saved panels, absolute or `~/`; ignored with a warning in layer 3 | `[]` |
| `theme_dirs` | folders holding themes, absolute or `~/`; ignored with a warning in layer 3 | `[]` |
| `skin_dirs` | folders holding skins, absolute or `~/`; ignored with a warning in layer 3 | `[]` |

**Settings are kept per engine.** Each engine's settings sit under its own name, and merging goes one level deeper
than today: `tts.<engine>` blocks merge per key across layers, and only the selected engine's block is passed to it.

```json
"tts": {
  "engine": "voxtral",
  "speed": 1.18,
  "kokoro":  { "voice": { "af_heart": 0.55, "bf_emma": 0.45 }, "lang": "en-gb" },
  "voxtral": { "voice": "<voice id>" }
}
```

A layer that sets only `"engine": "voxtral"` gets the voxtral block from the layers beneath it. `speed` stays
engine-independent and is passed to every engine; an engine that cannot vary speed ignores it.

**Old shape.** A `tts` section with `voice` or `lang` at its top level (today's shape) is read as the `kokoro` block,
with a startup warning naming the file and the new shape. Nothing is rewritten automatically.

## Engines

Replaces `specs/orb/spec.md` Swappable speech engines (the defaults and their tuning carry over).

### Kinds

| Kind | In | Out | Built-in |
|---|---|---|---|
| `stt` | 16 kHz mono samples | text | `moonshine` |
| `tts` | one sentence of text | samples and a sample rate | `kokoro` |
| `wake` | 16 kHz frames | a score per frame | none (Wake word, Models) |
| `vad` | 16 kHz frames | speech probability per frame | `silero` |
| `voiceprint` | 16 kHz samples | an embedding vector | `resemblyzer` |

`vad` and `voiceprint` serve the wake word only and are chosen inside the `wake` section.

### An engine is a folder

```
<root>/<kind>/<name>/
  engine.json    { "name", "version", "kind", "run": [...], "env": [...], ...kind fields }
  ...            whatever "run" needs
```

- `run` is the command that starts the engine, as an argument list resolved from the engine's own folder, for
  example `["uv", "run", "--script", "engine.py"]`. A Python engine declares its dependencies inline (PEP 723), so
  agent-orb never installs them. Any language works.
- `env` lists the environment variables the engine needs (`["MISTRAL_API_KEY"]`). A missing one fails the engine's
  load with a message naming it; `orb doctor` reports it.
- Kind fields: `stt` may declare `max_seconds`, the longest clip it accepts; the orb cuts longer clips into pieces at
  quiet points (today's long-hold logic, moved out of `moonshine.py`). `wake` and `vad` declare `frame_samples`.
- The engine gets `ORB_ENGINE_CACHE`, a per-engine folder under `~/.cache/voice-engines/<kind>/<name>/`, for model
  downloads.

### Discovery

1. `~/.config/voice-engines/<kind>/<name>/` — the user's, shared with any other tool on the machine.
2. `<plugin>/engines/<kind>/<name>/` — built-ins.

First match wins. The repo is never searched.

### Protocol

One process per engine per orb, started when the orb starts and kept warm. JSON lines on stdin and stdout; stderr
goes to the orb's log. Audio is little-endian float32 PCM, base64 in a `pcm` field.

| Request | Reply |
|---|---|
| `{"op": "load", "settings": {...}}` (first, once) | `{"ok": true}` or `{"ok": false, "error": "..."}` |
| `{"op": "render", "id", "text"}` (tts) | `{"id", "rate", "pcm"}` |
| `{"op": "transcribe", "id", "pcm"}` (stt) | `{"id", "text"}` |
| `{"op": "frame", "pcm"}` (wake, vad) | `{"score": 0..1}` |
| `{"op": "embed", "id", "pcm"}` (voiceprint) | `{"id", "vector": [...]}` |
| any request | `{"id", "error": "..."}` on failure |

Requests are answered in order. A process that exits is a failed engine. The orb keeps at most one `render`
outstanding, one sentence ahead of playback, so after Stop at most one sentence finishes rendering and is dropped:
the next reply never waits behind a discarded backlog, and a cloud engine is not billed for one.

### Failure

A selected engine that is missing, fails to load, or dies falls back to that kind's built-in
default, and the orb shows why in its header until it is fixed. A failed `wake`, `vad` or `voiceprint` engine turns
the wake word off for the session and says so; push-to-talk keeps working. This replaces today's "stops startup, no
silent fallback": the fallback is never silent.

### Built-ins move onto the contract

`moonshine`, `kokoro`, `silero` and `resemblyzer` become folders under `<plugin>/engines/`. The `fake` test engines
become folders too. The in-process loader (`orb/engines/__init__.py`) and the per-engine extras in `pyproject.toml`
are deleted once both gates pass:
- **Kokoro:** time to first audio and render time per sentence, measured on the MacBook Air (M1, 8 GB), are no
  worse than the in-process engine's on the same sentences.
- **Moonshine:** moves only after the long-hold fix (`ebbc4e6`) is confirmed on a real hold of a minute or more.

## Skins

Replaces `specs/orb/spec.md` Orb skins.

### `skin.json`

```json
{
  "name": "nebula-orb",
  "version": "1.0.0",
  "canvas": "2d",
  "states": ["idle", "listening", "transcribing", "thinking", "speaking"],
  "options": {
    "background": { "label": "Background", "choices": ["nebula", "hud", "eye"], "default": "nebula", "key": "KeyB" }
  }
}
```

- `canvas`: `"2d"` (default) or `"webgl"`. For `webgl` the skin gets a WebGL2 context.
- `states`: the states the skin draws. Absent means the five above, which is what every skin today draws.
- `options`: each option is a list of choices with a default, and optionally a key that cycles it while the window
  has focus. The orb shows them in its bar. A choice is saved to layer 4 under `skin_options.<skin>`. An option whose
  saved value is no longer a choice uses its default.

### `skin.js`

Sets `window.orbSkin = { init?(canvas, ctx), draw(ctx, frame) }`.

- `init`, optional, runs once after the canvas is created. A WebGL skin builds its scene here.
- `frame` gains `options` (the current choice per option) beside `state`, `level`, `time`, `width`, `height`.
- Files in the skin's own folder are served at `/skin/<path>` under the page token, so a skin can load its own
  modules, shaders and vendored libraries. Nothing outside the folder is served. No network, as before.

### States

The orb's states: `idle`, `wake_word`, `listening`, `transcribing`, `thinking`, `speaking`, and later `working`,
`waiting`. A state a skin does not list is drawn as its stand-in:

| State | Stand-in |
|---|---|
| `wake_word` | `listening` |
| `working` | `thinking` |
| `waiting` | `idle` |

So `pulse` and every existing skin keep working unchanged.

### Discovery and failure

Any folder in `skin_dirs`, then `~/.config/orb/skins/<name>/`, then built-ins; a skin that is missing, invalid or throws falls back to
`pulse` and the orb says why. A `webgl` skin on a browser without WebGL2 falls back the same way.

## Themes

A skin draws the orb. A theme styles everything around it: the orb's own controls (the corner pill, the mic and stop
buttons, the caption, the panel tabs) and every panel an agent shows. The two are separate pages already (each panel
is a sandboxed frame), so a theme is two stylesheets, not one.

### A theme is a folder

```
~/.config/orb/themes/<name>/
  theme.json     {"name", "version", "description", "tokens": {"bg": "#04070d", "accent": "#00e5ff", ...}, "skin", "tts"}
  orb.css        optional: the orb's controls
  panels.css     optional: every panel
  (fonts, images: served beside them)
```

- **Tokens** are the theme's colors and fonts, written once and emitted into both pages as CSS variables
  (`--orb-stage` behind the orb, `--orb-bg` for panels, `--orb-surface`, `--orb-fg`, `--orb-dim`, `--orb-accent`,
  `--orb-line`, `--orb-edge`, `--orb-ok`, `--orb-warn`, `--orb-err`, `--orb-font`, `--orb-display`, `--orb-mono`). A theme with
  only tokens recolors everything. A value may not carry `;`, braces, angle brackets or a backslash.
- **`orb.css`** loads after the orb's own stylesheet, so it wins. **`panels.css`** is injected into each panel
  after the default panel stylesheet and before the page's own styles, so the theme replaces the default but a page
  that carries its own styling (a dashboard ported from elsewhere) still wins and keeps its look.
- A selector that names something the orb no longer has styles nothing; it never breaks the orb. The orb documents
  the ids and classes its controls use, as any web app does, and promises no more than that.

### Built-in and choosing

- `default` is today's look: the original agent-orb controls and panel stylesheet, unchanged. Shipping it as a theme
  needs one cleanup: the controls' stylesheet names its variables `--bg`, `--accent`, ... and hardcodes about twenty
  colors; both move onto the shared `--orb-*` tokens first, so one token set feeds both pages.
- Chosen per repo: config key `theme` (layer 4), default `default`, with a theme menu in the corner pill beside the
  skin menu. Switching reloads the window and every open panel.
- One theme at a time styles both pages. A theme that wants to leave the controls alone ships no `orb.css`.
- Discovery and failure mirror skins: any folder in `theme_dirs`, then `~/.config/orb/themes/<name>/`, then built-ins;
  where a name exists in more than one, the first wins. A theme that is missing or has
  an invalid `theme.json` falls back to `default` and the orb says why.
- The plugin ships only `default`. Any other theme is versioned in its author's own repo and installed as a copy,
  like a personal skin (decision 4).

### A theme may name a skin (2026-10-16)

The link runs one way: a theme may name a skin; a skin never names a theme or a stylesheet. `theme.json` takes an
optional `"skin": "<name>"`, applied only at the moment the theme is switched to, which saves that skin as the repo's
`skin`. Switching the skin afterwards changes the skin alone and the theme stays; nothing re-applies the theme's skin
on load. A named skin that is not installed leaves the current skin in place and the orb says why. `default` names
`pulse`.

### A theme may name speech (2026-10-20)

The same one-way link, for the voice: `theme.json` takes an optional `"tts"`, the config's `tts` section without
`speed` (`{"engine": "<engine>", "<engine>": {"voice": "<voice id>"}}`), checked as strictly as that section and
refused if it names anything else or a credential. It is applied only at the moment the theme is switched to, over
the current `tts`, and saved as the repo's `tts` beside the skin. Changing the engine or voice afterwards keeps the
theme; nothing re-applies the theme's voice on load. An engine that is not installed or fails to load leaves the
current engine and voice speaking and the orb says why, as for a missing skin; `orb doctor` reports a theme naming
an engine that is not installed. No other config key comes from a theme.

## Voice toggle

New.

- A speaker button in the orb's bar, and `mute_key` while the window has focus, toggle `speak` and save it to layer 4.
- `orb voice on|off|toggle [--agent claude|copilot]` does the same from the terminal, through the running server
  (token from `server.<agent>.json`); with no orb running it writes layer 4 and says so.
- Muting while speaking stops the speech at once.
- While muted, replies are not rendered at all, so a cloud engine is not called. The transcript still shows them and
  they count as missed as today. Unmuting does not speak replies that arrived while muted.
- The header shows muted.

## Wake word

New. Off unless `wake` is set.

```json
"wake": {
  "engine": "openwakeword",
  "model": "hey_jarvis",
  "threshold": 0.5,
  "vad": "silero",
  "silence_s": 1.5,
  "prespeech_s": 4,
  "follow_up_s": 8,
  "voice_auth": false,
  "voice_auth_threshold": 0.72
}
```

`model` is required and has no default (amended 2026-10-16): agent-orb assumes no phrase, so a user who turns the
wake word on names the model they installed. The other keys default as shown.

### Flow

1. With `wake` set, the page opens the mic when it loads and streams 16 kHz frames to the server continuously, over
   the same worklet push-to-talk uses. The header shows "listening for <model>"; the browser's mic indicator is on.
2. While the orb is **idle**, the server feeds frames to the wake engine. A score at or above `threshold` on two
   frames in a row fires it; a cooldown of one second follows. In every other state frames are discarded, so the
   orb's own speech cannot wake it.
3. On firing the orb shows **wake_word**, then **listening**. The recording starts with the last second of audio
   before the firing, so a command spoken in the same breath ("hey Jarvis, open the board") keeps its first words.
   The `vad` engine runs only on audio after the firing; fed the wake phrase, it would hear the pause after it as
   the end of speech. A leading wake phrase in the transcribed text is stripped. The `vad` engine ends the
   recording after `silence_s` of trailing silence. Nothing heard within `prespeech_s` returns to idle, and a
   recording holding only the prebuffer is dropped rather than sent.
4. With `voice_auth` on, the clip's voiceprint is compared with the user's (below). Below the threshold the clip is
   dropped and the orb returns to idle, saying so in the transcript.
5. From here it is push-to-talk's path: transcribing, the request, thinking, speaking.
6. **Follow-up window.** When the reply to a request made by the wake word (or by a follow-up) finishes playing,
   for `follow_up_s` the orb listens without the wake word: speech the `vad` engine detects starts step 3 directly.
   A reply to the button or to a turn typed in the terminal opens no window. `0` turns it off.

Push-to-talk keeps working throughout and takes precedence: pressing it cancels a wake recording in progress.
One request in flight still holds: a busy orb ignores the wake word.

### Voice authentication

- `orb voice enroll` opens the orb window into an enrollment view: the user reads five short phrases, and the
  embeddings' average is saved as the voiceprint.
- The voiceprint is the person's, not the repo's: `~/.config/orb/voiceprint/<voiceprint engine>.json`, readable by
  the user only. It is biometric data and never goes in a repo or a log.
- Threshold: cosine similarity at or above `voice_auth_threshold` passes.
- `voice_auth: true` with no voiceprint enrolled keeps the wake word off and tells the user
  to enroll. It does not fail open.

### Models

agent-orb ships no wake model. openWakeWord's code is Apache 2.0, but its pre-trained models (including
`hey_jarvis`) are CC BY-NC-SA 4.0, non-commercial, because of their training data. A user installs the engine and a
model into `~/.config/voice-engines/wake/openwakeword/`, and `orb doctor` names the model's licence where it can.
A model for a new word is trained with openWakeWord's own tooling; this spec does not include training.

## Checks

**Unit tests** (pytest, Windows and macOS), added to `specs/orb/spec.md` Checks:
- config: the four layers in order; per-engine blocks merging per key; a layer that sets only `engine` inheriting
  that engine's block; the old flat `tts` shape read as `kokoro` with a warning; credential-named keys refused in
  every layer; orb-written values landing in layer 4 only;
- engines: discovery order (user before built-in, repo never); `engine.json` validation; a missing `env` variable
  named in the failure; the protocol against a fake engine process (load, each op, an error reply, the process
  dying); fallback to the built-in with the reason kept; clip splitting driven by `max_seconds`;
- skins: `canvas`, `states` and `options` validation; the state stand-in table; a saved option no longer among the
  choices using its default; `/skin/<path>` refusing anything outside the skin folder;
- voice toggle: muted replies never sent to the engine; muting mid-speech stops playback; unmuting speaks nothing
  queued;
- wake word: firing on two consecutive frames and not one; frames discarded outside idle; the cooldown; prespeech
  timeout; the prebuffer kept in the recording but not fed to `vad`; a prebuffer-only recording dropped; a leading
  wake phrase stripped from the text; follow-up window opening after playback ends and closing after `follow_up_s`; push-to-talk cancelling a
  wake recording; voice auth rejecting below threshold; voice auth on with no voiceprint keeping the wake word off.

**Behavioral checks** (`orb doctor`, read-only): each selected engine is found, its `env` is set, and it loads; the
selected skin validates; with `wake` set, the model file exists and a voiceprint exists when `voice_auth` is on.

## Open questions (resolved by throwaway tests at the start of the plan)

1. **Does the mic keep streaming with the window in the background?** The wake word matters most while the user is
   in the terminal, with the orb window behind it or minimized. Whether a Chrome `--app` window keeps `getUserMedia`
   and the AudioWorklet delivering frames when occluded or minimized is unmeasured, on macOS and on Windows. If it
   does not, the wake listener moves server-side (capture with `sounddevice`) and the page only shows state.
2. **Wake engine speed through the protocol.** 80 ms frames as base64 JSON lines, one round trip per frame, on the
   MacBook Air: does the wake engine keep up with real time alongside the warm STT and TTS engines?

## Decisions (2026-10-16)

1. A failed engine falls back to its kind's built-in with the reason shown, instead of stopping startup (Engines,
   Failure).
2. Voice auth on with no voiceprint keeps the wake word off and asks the user to enroll; it never fails open (Wake
   word).
3. The speed slider writes per repo (layer 4), not for every repo (layer 2).
4. The plugin ships no personal skin. The author's own look (the first `webgl` skin, which proves the contract) is
   versioned in the author's own repo and installed into `~/.config/orb/skins/` as a copy, like decision 6's engines.
5. Wake-word models are user-installed only, given their non-commercial licence. Whether a use is commercial is the
   user's own licence call.
6. agent-orb ships only its defaults (`moonshine`, `kokoro`, `silero`, `resemblyzer`). Any other engine, cloud
   voices and wake engines included, is versioned by whoever writes it, in their own repo, and installed into
   `~/.config/voice-engines/` as a copy. agent-orb carries no vendor integration it does not default to.

## Build notes (2026-10-16)

Where the build departed from the text above, and why. Each is reversible. Branch `customization-specs`; every
phase is its own commit; 490 tests pass on the MacBook Air.

1. **Moonshine moved onto the engine contract with Kokoro, before the real-hold confirmation** (plan 2.4). Keeping
   the in-process loader for one engine would have meant two code paths in the server for one job. The long-hold
   cutting moved unchanged into `orb/engines.py` (`split_at_quiet_points`, now driven by `max_seconds`) and the
   clean-up unchanged into the engine script, with their tests; a 26 s synthetic clip transcribed in 0.88 s through
   the folder engine. The real-hold check now covers both at once.
2. **Kokoro's model files moved** from `~/.cache/orb/tts/kokoro/` to `~/.cache/voice-engines/tts/kokoro/`, the
   engine's own cache. On this Mac they were copied over, so no download; another machine downloads once (335 MB).
3. **Speed travels with each `render` request** (`{"op": "render", "text", "speed"}`) rather than in `load`, so the
   slider takes effect on the next sentence without reloading the engine. The protocol table above lacks it.
4. **Inside the follow-up window the VAD alone decides.** The wake phrase spoken there is speech like any other
   and is stripped from the transcript; the trigger is not consulted until the window closes or a recording begins.
5. **Every frame goes through the wake engine, in every state.** The end-to-end run found the orb firing a second
   time right after a reply: openWakeWord keeps a window of recent audio, and one that stopped at the phrase (frames
   were withheld while recording and speaking) scored the next frame high. Scores are acted on only while idle.
6. **Frames from two windows are taken one at a time** (a lock); the same run, with the page and a test client both
   streaming, raced on the recording. Two windows streaming at once still interleave their audio into one engine,
   which degrades detection; one window with the mic is the intended case.
7. **The mic is reopened while the wake word is on** after a device change, or on a three-second check when the
   stream has dropped; the page used to release it on device change and never reacquire.
8. **Chrome's mic permission is per origin, and the origin is the port**: a repo's orb gets a new port per agent
   on first use, so the first wake-word session in a repo prompts once. Nothing to change; it is why `stable_port`
   exists.
9. **The orb cannot be woken by its own speakers on this Mac**: the page asks for echo cancellation, and Chrome
   subtracts the system's output from the mic. The end-to-end wake test therefore fed frames over the socket. A real voice from
   across the room is still the plan's Phase 5 "done when".
10. **Not done:** the Windows pass (6.1); the real-hold check; a second person's voice against voice
    auth; enrolling a voiceprint (`orb voice enroll` with the orb open, five phrases).

11. **Found in the first live test (2026-10-16 morning):** a key click followed by a second of silence before the
    words made Moonshine return nothing; the engine now cuts every pause over 0.3 s to 0.3 s before decoding. The
    follow-up window opened after every spoken reply, so dictation into the terminal right after one was captured
    twice; it now opens only after a reply to a request made by the wake word. `silence_s` went from 0.9 to 1.5:
    a natural pause ended the recording mid-sentence.

### Phase 0 findings, in one line each

- 0.1: a background, minimized or hidden `--app` window keeps the mic at full rate, display asleep included; page
  timers are throttled, the audio path is not.
- 0.2: the wake engine answers an 80 ms frame in 6.7 ms over JSON lines, 8 % of a core; no protocol change needed.
- 0.3 and the 2.3 gate: Kokoro through the folder engine renders within 2 ms a sentence of in-process, measured
  paired to remove the Air's thermal drift.
