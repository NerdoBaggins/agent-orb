# Intent: Make the orb yours — skins, voices, wake word, per repo
Author: Alberto Gonda. Status: accepted (2026-10-16).

Amends `specs/orb/intent.md`: two of its constraints are reversed below.

## Problem
The orb works, but most of it is fixed. A skin can only draw a 2D frame and cannot offer the user any choices.
Adding a speech engine means editing the plugin. Config is one user file plus the repo's committed file, so there is
no place for a user's own choice in one repo, and a repo that changes engine inherits another engine's settings. The
voice is on whenever the orb is open. There is no hands-free way to talk to it.

## Proposed outcome
- **Skins with options.** A user adds their own orb look by dropping in a skin. A skin can declare options (for
  example a choice of backgrounds) that the user changes from the orb while it runs, and the orb remembers them. The
  default look stays `pulse`.
- **Engines anyone can add.** Speech-to-text, text-to-speech and wake-word engines are folders the user installs once
  in a shared per-user location, `~/.config/voice-engines/<stt|tts|wake>/<name>/`, which the orb reads before its own
  built-ins. agent-orb does not own that folder: other tools on the machine may read the same engines. Each engine
  brings its own dependencies; agent-orb never installs them.
- **Per-repo choice, kept out of the repo.** The user picks skin, engines and voice for each repo. Config layers,
  weakest to strongest: built-in defaults, user (`~/.config/orb/config.json`), the repo's team file if it has one
  (`<repo>/.orb/config.json`, optional), the user's choices for that repo (`~/.config/orb/repos/<repo id>/config.json`).
  Engine settings are kept per engine, so switching engine never carries another engine's settings along.
- **A voice toggle.** The user mutes and unmutes the orb's speech without closing it.
- **A wake word, opt-in.** Off by default. A user can turn it on for a repo and choose the word, which is a wake
  engine model. Two options go with it: voice authentication (only the enrolled user's voice is answered) and a
  follow-up window (after a reply, the user can speak again without the wake word). Push-to-talk keeps working.

## Affected users and systems
- The author first, then anyone using the orb, in any repo.
- Existing skins (`pulse` and users' own) must keep working unchanged.
- The built-in engines (Moonshine, Kokoro) move onto the same engine contract as added ones.

## Constraints
- Nothing personal is committed. Per-repo choices, voiceprints and installed engines live in the user's home folder.
  The repo's team file stays optional.
- Credentials come only from the environment. An engine names the variables it needs; config never holds them.
- A repo can neither name nor ship an engine, a skin, a theme or a page: opening the orb in a cloned repo must not run code from that repo or choose which of the user's code runs.
- Defaults stay local and push-to-talk. Cloud engines and the wake word are per-user opt-ins.
- Windows and macOS, Claude Code and Copilot CLI, as before.
- No coordination with other speaking tools on the machine. If the orb and something else speak at once, both are
  heard; the user started both.
- Moving Kokoro onto the new contract must not slow it: time to first audio is measured against today's on the
  MacBook Air before the old path is removed. Moonshine moves only after the long-hold transcription fix is
  confirmed on a real hold.

## Reverses (from `specs/orb/intent.md` and `specs/orb/spec.md`)
- "Speech stays on the machine in both directions" and the non-goal "Cloud speech in either direction": a user may
  choose a cloud engine. The defaults stay local.
- "Push-to-talk only … no wake word" and the non-goal "A wake word, an always-on mic": a user may opt in to a wake
  word per repo. While it is on the mic is open, and the window says so.
- Spec, Configuration: "repo values override user values" becomes the four layers above, with the user's per-repo
  choice strongest.
- Spec, Swappable speech engines: engines become folders run as their own process, discovered in the shared
  location first, rather than modules inside the plugin.

## Open questions
1. Engine contract: the process protocol (text in, audio out; audio in, text out; audio in, "heard it" out), what
   `engine.json` declares (needed environment variables, an STT engine's longest accepted input), and whether a
   missing or broken engine still stops startup (today's rule) or falls back to the default with the reason shown.
2. Skin contract: a WebGL canvas alongside 2D, how options are declared and shown, and how the new states
   (`wake_word`, `working`, `waiting`) map onto existing ones for skins that do not handle them.
3. The author's own look: a built-in skin that ships to everyone, or one he keeps in
   `~/.config/orb/skins/`? Its name.
4. Wake-word models: the licence of openWakeWord's pretrained models, and how a user gets a model for a new word.
   Belongs next to the existing espeak-ng licence question.
5. Voice authentication: how a user enrolls, and where the voiceprint is kept (biometric, per user, never in a repo).
6. The orb hearing itself: with no voice authentication, does the mic ignore audio while the orb is speaking?
