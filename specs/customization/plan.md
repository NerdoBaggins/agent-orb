# Plan: Make the orb yours

Author: Alberto Gonda. Status: approved (2026-10-16); built 2026-10-16 on branch `customization-specs`, Phases 0–5 and 6.2 done, 6.1 (Windows) and the checks named in the spec's build note 10 open. Spec: `specs/customization/spec.md` (approved 2026-10-16).

The ground rules of `specs/orb/plan.md` hold: every plugin test runs in a throwaway repo outside agent-orb with the
plugin installed from the local folder; each step ends in commits and a "done when" that is checked, not assumed;
throwaway spike code stays untracked, with each finding summarized in the spec; a finding that changes the
spec goes back to the author before the build relies on it.

This work is built on the MacBook Air (M1, 8 GB) first, because both speed gates in the spec are measured there.
Windows is checked at each phase's end on a Windows desktop.

Phases 1 through 5 each stand on their own and are shippable alone. Their order is chosen so that each lands on
proven ground: engines before the wake word (which is three more engines), skins before the wake word (which adds a
state), config before everything (every phase writes to layer 4).

## Phase 0: throwaway tests

Each answers one open question of the spec before anything is built on it.

**0.1 Mic in a background window** (spec open question 1)
- A page in a Chrome `--app` window opens `getUserMedia`, runs the push-to-talk AudioWorklet, and counts frames per
  second to a visible counter and the console.
- Put the window behind the terminal; minimize it; hide the app (macOS ⌘H); switch desktops; lock and unlock the
  screen. On Windows: behind another window, minimized, on another virtual desktop.
- Record frames per second in each state, and whether the AudioContext suspends.
- Done when: the finding says, per OS, whether a background window keeps delivering frames. If it does not on either
  OS, the spec's wake-word flow moves capture server-side (`sounddevice`) and the page shows state only; that is a
  spec change, taken back to the author before Phase 5.

**0.2 Wake engine through the protocol** (spec open question 2)
- A throwaway openWakeWord engine process speaking the spec's JSON-lines protocol, fed 80 ms frames of recorded
  audio holding "hey Jarvis" among other speech, with Moonshine and Kokoro loaded warm in the same orb process.
- Measure on the MacBook Air: round-trip per frame, CPU of the engine process at rest and under speech, and whether
  the frame queue ever grows. Record the detection latency from the end of the phrase.
- Done when: the engine keeps up with real time with headroom (median round trip under 40 ms for an 80 ms frame) or
  the finding names the fix (larger frames, binary frames instead of base64, or a shared-memory ring).

**0.3 Kokoro baseline** (the spec's Kokoro gate needs a before)
- Time to first audio and render time per sentence for the in-process Kokoro engine, on a fixed set of ten
  sentences, on the MacBook Air, three runs each.
- Done when: the numbers exist before Phase 2.3 starts.

## Phase 1: config layers

**1.1 Four layers.** `~/.config/orb/repos/<repo id>/config.json` as the strongest layer; `merge` over four; the
credential check on every user-written layer; unknown-key warnings naming the file.
**1.2 Per-engine settings.** `stt.<engine>` and `tts.<engine>` blocks merging per key; only the selected engine's
block passed to it; `speed` engine-independent; the old flat `tts` shape read as `kokoro` with a warning naming the
file and the new shape; `config.example.json` and the README updated.
**1.3 Layer 4 is where the orb writes.** The speed slider moves from layer 2 to layer 4; `save_user_speed` becomes a
general "save a key for this repo" used by later phases.
Done when: a user file naming Kokoro's voice and a per-repo file saying only `"engine": "voxtral"` load as Voxtral
with no Kokoro keys in its settings; moving the slider in one repo leaves another repo's speed alone; the old shape
warns and still speaks.

## Phase 2: engines as folders

**2.1 Contract and runner.** `engine.json` validation; discovery (`~/.config/voice-engines/<kind>/<name>/`, then
`<plugin>/engines/`); the process runner (start, `load`, ordered requests, error replies, death detection,
`ORB_ENGINE_CACHE`, `env` check); fallback to the built-in with the reason in the header; `orb doctor` checks.
Fake engines become folders and drive the tests.
**2.2 First outside engine: Voxtral.** Written and versioned in the author's own repo, not
here; installed into `~/.config/voice-engines/` by copy. This phase ends when it speaks through the orb in a
throwaway repo with `MISTRAL_API_KEY` set, and the orb falls back to Kokoro, saying why, with it unset.
**2.3 Kokoro onto the contract.** `<plugin>/engines/tts/kokoro/` as a folder with inline dependencies; the same
numbers as 0.3, three runs. Gate: time to first audio and per-sentence render no worse than 0.3. If it misses, stop
here and find out why (process start, base64, PCM copying) before any further move.
**2.4 Moonshine onto the contract.** Gated on the author having confirmed the long-hold fix (`ebbc4e6`) on a real hold
of a minute or more. The clip splitting moves out of `moonshine.py` into the orb, driven by `max_seconds`.
**2.5 Delete the old path.** `orb/engines/__init__.py`'s in-process loader, the per-engine extras in
`pyproject.toml`, and the `orb open` extras sync. `silero` and `resemblyzer` are not built here; Phase 5 adds them.
Done when: both built-ins run as folders, the old loader is gone, `orb doctor` reports every selected engine, and
the Windows desktop runs the same.

## Phase 3: skins with options

**3.1 Manifest.** `canvas`, `states`, `options` validation; the state stand-in table; `/skin/<path>` serving the
skin's own folder and nothing outside it.
**3.2 Page.** A WebGL2 context when asked; `init`; `options` in the frame; the options in the orb's bar with their
keys; a choice saved to layer 4 under `skin_options.<skin>`; a saved value no longer among the choices using the
default; WebGL unavailable falling back to `pulse` with the reason.
**3.3 First WebGL skin.** The author's own look, versioned in the author's own repo; installed by copy into
`~/.config/orb/skins/`. It declares a `background` option (nebula, hud, eye) on `KeyB`. It proves
the contract; nothing of it is committed here.
**3.4 `skins/README.md`** rewritten for the new manifest, with a 2D and a WebGL example.
Done when: `pulse` and the minimal example in the README run unchanged; the author's skin runs with its three
backgrounds, the choice survives a restart, and a skin that throws in `init` falls back with the reason.

## Phase 4: voice toggle

**4.1 Server.** `speak` in config; a muted orb never calls the TTS engine; muting mid-speech stops playback; nothing
queued is spoken on unmute; the header shows muted.
**4.2 Controls.** The speaker button in the bar; `mute_key`; `orb voice on|off|toggle` through the running server,
or writing layer 4 with no server running, saying which it did.
Done when: with a cloud engine selected and the orb muted, a reply produces no engine request; `orb voice on` from the
terminal unmutes a running orb.

## Phase 5: wake word

**5.1 Engines.** `wake`, `vad` and `voiceprint` kinds in the contract; `silero` and `resemblyzer` as built-in
folders; the openWakeWord engine versioned in the author's own repo and installed by copy; `orb doctor` checks for the
model file and the voiceprint.
**5.2 Capture.** Continuous frames while `wake` is set, from the page (0.1 passed) or server-side (0.1 failed);
frames dropped outside idle; the two-frame fire and the one-second cooldown; the `wake_word` state.
**5.3 Recording.** The prebuffer kept but not fed to VAD; `silence_s` ending; `prespeech_s` timeout; a prebuffer-only
recording dropped; the leading wake phrase stripped; push-to-talk cancelling a wake recording; a busy orb ignoring
the wake word.
**5.4 Follow-up window** after playback ends, `follow_up_s`, `0` off.
**5.5 Voice authentication.** `orb voice enroll` (five phrases in the orb window; the average embedding written to
`~/.config/orb/voiceprint/<engine>.json`, user-readable only); the threshold; voice auth on with no voiceprint keeping
the wake word off and saying so.
Done when: in a throwaway repo with `wake` set, "hey Jarvis, what time is it" spoken from across the room gets a
spoken answer with the orb window behind the terminal; the orb's own reply does not wake it; a second person's voice
is refused with voice auth on; a repo with no `wake` section shows no mic indicator.

## Phase 6: Windows pass and docs

**6.1 Windows.** Phases 1 through 5 exercised on a Windows desktop; `%USERPROFILE%\.config\voice-engines\` and
`\.config\orb\repos\`; the engine runner under Windows process semantics; a WebGL skin under Edge `--app`.
**6.2 README**: the four layers, writing an engine, writing a skin with options, the voice toggle, turning on the
wake word and its model licence, enrolling a voiceprint.
Done when: `orb doctor` passes on both OSes with the full config, and the README's own examples work as written.

## Out of the plan

- Everything in the author's own repo: its engines, skins and the copy step that installs them. That is its own
  plan in that repo.
- Producing the `working` and `waiting` states.
- Training a wake-word model.
- Barge-in by voice.
