# Intent: Orb, a voice assistant with drop-in panels for coding agents
Author: Alberto Gonda. Status: accepted (2026-10-05). Amended by `specs/customization/intent.md` (accepted 2026-10-16).

## Problem
Working with a coding agent (Claude Code or GitHub Copilot CLI) means reading and typing in a terminal. There is no
way to talk to the agent, hear it answer, or ask it to show something visual and have it explained aloud.

A first draft scoped this as a single dashboard inside the Chinook2 harness. That was too narrow. A dashboard is one
thing the orb should show, not the product, and the tool should not belong to one repo.

## Proposed outcome
- A user can add the orb to any repo and use it from Claude Code or Copilot CLI, on Windows or macOS.
- Opening it shows an orb that fills its window. The user holds a mic button, or a key while the window has focus,
  and speaks. The agent does what was asked and answers in a natural local voice, and the orb shows what it is
  doing: idle, listening, transcribing, thinking, speaking.
- While the orb is busy, the mic button becomes Stop: it ends the speech, or interrupts the agent's work.
- When the user asks to see something, the agent writes a panel and it slides in on the right; the orb shrinks
  to make room. Several panels can be open as tabs, and the user can close them by voice or by button.
- A panel is any page the agent writes, on the fly or saved for reuse. It gets the orb's abilities for free:
  being opened by voice, "explain this" buttons whose answers are spoken, and reading text aloud.
- The orb's look is a replaceable skin, with a distinctive default skin.
- The speech engines can be swapped with minimal effort, so other options can be tried later.

## Affected users and systems
- The author first; then colleagues and any developer who uses Claude Code or Copilot CLI and wants voice.
- Repos that adopt it, including Chinook2 through its AI coding harness, which would use the orb rather than hold it.
- Claude Code (through its channel mechanism, research preview) and Copilot CLI (through its `--ui-server` session).

## Constraints
- Works on Windows and macOS.
- Works under both Claude Code and Copilot CLI.
- Usable in any repo by anyone. Nothing user-specific is committed: per-user settings (voice, skin, keys) live
  outside the repo, and credentials come only from the environment.
- Speech stays on the machine in both directions. Defaults: Moonshine for speech-to-text and Kokoro for the voice.
  Both must be swappable by configuration.
- Push-to-talk only: a mic button, and a key held while the orb window has focus. No global hotkey, no wake word.
- The agent writes every spoken reply.
- It must not live in the Chinook2 harness, which holds Chinook2 tooling only.

## Open questions
1. Packaging: a plugin (which both Claude Code and Copilot CLI now support) is the leaning, since the orb needs a
   skill, hooks and an MCP server together. Is one package able to serve both agents?
2. Interrupting the agent: can a running Claude Code turn be stopped from the orb on Windows and on current macOS,
   and what does Stop do where it cannot?
3. Is Moonshine and Kokoro speed acceptable on Windows and on a Mac?
4. With two agent sessions open, is there one orb per session, or one orb the user points at a session?
5. Are colleagues an audience for the first version, or is it the author first, then others?
6. espeak-ng (used by Kokoro) is GPL-3: is that acceptable if the orb is shared beyond internal use?
7. Proven so far (2026-10-05): a Claude Code channel delivered a test message into a live session on this Windows
   machine. That proves delivery only, not the rest.

Resolved: the repo is `agent-orb`, at `C:\src\agent-orb`.
