# Plan: Orb

Author: Alberto Gonda. Status: approved (2026-10-05). Spec: `specs/orb/spec.md` (approved 2026-10-05).

Every test of the plugin runs the way a user would meet it: in a throwaway repo outside agent-orb (a scratch folder
with `git init`), with the plugin installed from the local agent-orb folder, and a Claude Code or Copilot session
started there. The plugin is never installed in agent-orb itself, so nothing can pass only because it runs inside its
own source tree, and a broken hook never breaks the session developing it.

Each step ends in one or more commits and has a
"done when" that is checked, not assumed. Windows first; macOS is checked at
the throwaway tests and again at the end.

## Phase 0: throwaway tests

Each answers one spec open question before anything is built on it. Throwaway code stays untracked, and each
finding is summarized in the spec's open questions. A finding that changes the spec goes back to the author
before the build relies on it.

Every test targets a throwaway session started for it, never a working session (a `/compact` or an Escape sent to the
wrong one would hit a real conversation). Anything installed into an agent is installed at the narrowest scope that
works and removed afterwards; the finding records which.

**0.1 Plugin manifests** (open question 1)
- A minimal plugin holding one skill, a `Stop` hook that writes a marker file, and an MCP server that answers one tool.
- Install it in Claude Code and in Copilot CLI from a local folder.
- Record: does one manifest folder serve both, or does each need its own; does each agent load the skill, fire the
  hook, start the MCP server; which plugin-root variable each exposes to hooks, MCP config and skill text; the
  `--dangerously-load-development-channels` form that loads a channel shipped in a plugin; and what the `Stop` hook
  costs at each turn end when no orb is running (it fires in every session with the plugin enabled, so it must be
  cheap).
- Done when: both agents load all three parts, or the finding names exactly which part fails and the workaround.

**0.2 Interrupting a Claude Code turn** (open question 2)
- Windows: from a separate process, find the Claude Code session's console, `AttachConsole` + `WriteConsoleInput` an
  Escape key event, without focusing the window. Test under Windows Terminal and conhost.
- macOS: AppleScript Escape to the session's tab, iTerm and Terminal.
- Done when: a long-running turn is cancelled from outside on each, or the finding says which cannot and the spec
  fallback (Stop ends speech only) applies there.

**0.3 Engine speed** (open question 3)
- Moonshine `base` on a set of 2 to 10 s clips; Kokoro with the default voice on short and long paragraphs.
- Measure on Windows and on a Mac: transcription time per clip, Kokoro time to first audio,
  real-time factor, load time and memory.
- Target: transcription under 1.5 s for a 5 s clip; first audio under 3 s.
- Done when: the numbers are recorded per machine; a machine that misses a target is named with the next option
  (smaller model, other engine).

**0.4 Slash commands and answer style** (open questions 4 and 5)
- Through the spike channel and the Copilot helper: send `/compact`-style and skill slash commands; record whether
  each runs as a command or arrives as text.
- Send ten varied requests with the draft skill text; record whether replies are plain spoken language.
- Done when: both are recorded; the skill text is adjusted if replies drift.

## Phase 1: skeleton

**1.1 Layout and tooling.** The plugin layout from 0.1, with `orb` reached through the skill's base directory;
`pyproject.toml`; pytest; `config.example.json`; README stub.
The repo has no remote; where it lives is the author's call, and CI on Windows and macOS is added once it has one.
Done when: the test suite runs locally on Windows, and on a Mac the next time one is used.

**1.2 Config and state.** User and repo config layering, validation, credential-key rejection, unknown-key reporting;
the per-user state folder and repo id; `server.json` and `channel.json` read/write with user-only permissions.
Done when: the unit tests named in the spec's Checks for config and state pass.

## Phase 2: text loop under Claude Code (no voice yet)

Proves the agent link end to end with typed input before speech is added.

**2.1 Orb server and page.** Python HTTP + WebSocket on `127.0.0.1`, per-launch token, Origin check; `orb open`
starts it, writes `server.json`, opens the Chrome `--app` window (platform adapter, Edge fallback). The page shows the
header and a placeholder orb. The second same-agent launch in a repo is reported, not started.
Done when: token and Origin unit tests pass; `orb open` opens one window on Windows; a second `orb open` reports.

**2.2 Channel.** Rewrite the spike to the spec: state-folder port and secret, JSON only, rejects any `Origin`,
`/health` (secret required) after handshake, exits on stdin close, retries a taken port.
Done when: the channel's rejection tests pass and a request posted by the orb server arrives in a live session started
with the flag from 0.1.

**2.3 Stop hook.** The gate (orb-channel origin + pending request id) and the reply post; exits 0 with no orb running.
Done when: the gate's unit tests pass (each silent case in the spec), and a request typed into a test-only input on the page
comes back as text in the page. The input is removed in Phase 3.

**2.4 Skill text.** How to open the orb, how to answer orb requests (from 0.4), the restart command when the channel
is not bound.
Done when: a fresh session told "open the orb" opens it without further help.

## Phase 3: voice

**3.1 Engine interfaces and fakes.** STT and TTS interfaces, loading by name, failure messages, fake engines.
**3.2 Moonshine and Kokoro engines**, with the clean-up and espeak-ng path handling from the spec.
**3.3 Push-to-talk.** Mic button and key, AudioWorklet resample, frame streaming, tap and cap limits, "didn't catch
that".
**3.4 Speaking.** Paragraph rendering with render-ahead, PCM over the WebSocket, Web Audio playback, level metering,
Stop ends speech.
Done when: on Windows, holding Space and asking a question gets a spoken answer from the Claude Code session, and Stop
during speech silences it at once.

## Phase 4: Copilot

**4.1 Copilot helper.** Node helper around Copilot's SDK (attach, send, abort, expected-session check), SDK location
adapter, the poll after a `sendAndWait` timeout.
**4.2 Agent selection.** `--agent`, probing both, the header states.
Done when: the same voice loop works against `copilot --yolo --ui-server --port 4321 --continue`, and Stop while
thinking aborts the Copilot turn.

## Phase 5: Stop while thinking under Claude Code

The interrupt adapters proven in 0.2, behind one interface; the fallback message where none works.
Done when: Stop during a long Claude Code turn cancels it on Windows, and the request is marked interrupted.

## Phase 6: panels and skins

**6.1 Panels.** `orb panel show|close`, `orb panels list`, sandboxed iframe on its own origin,
injected `orb.send` (user-activation gated, `busy` while busy, `speak` option), `orb.state`, default stylesheet,
tabs and layout animation, close by voice, the transcript panel.
**6.2 Skins.** Skin loading and fallback, the `pulse` skin, `skins/README.md`.
Done when: a throwaway test panel's button sends a request and the reply is spoken; with `speak: false` it is not; a
script calling `orb.send` without a click is refused; asking the agent for a panel it has not seen before ("show me a
chart of X") produces one without further instruction.

## Phase 7: doctor, docs, macOS

**7.1 `orb doctor`**: the read-only behavioral checks in the spec.
**7.2 README**: install, start commands for each agent, config, writing a skin, how panels work.
**7.3 macOS pass**: the full loop under both agents on a Mac; fix what differs.
Done when: `orb doctor` passes on Windows and a Mac, and the end-to-end loop works under both agents on both.

## Out of the plan

- Any specific panel (a Jira board and the like): asked of the agent once the orb works.
- Sharing with colleagues, a release, the espeak-ng licence check.
