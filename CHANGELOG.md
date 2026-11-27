# Changelog

Every change a user of agent-orb can notice; a pull request no user would notice has no entry. Each pull request
that has one adds it under Unreleased.

## Unreleased

### Added

- Streaming speech, off until you turn it on. An engine that says `"stream": true` in its `engine.json` can send a
  sentence's audio in pieces, and with `tts.stream` set (the bar's `stream speech (start sooner)` box) the orb starts
  playing once a short lead of audio is buffered instead of waiting for the whole sentence, so speech starts sooner
  and a late piece does not leave a gap.
  The box is greyed, with the reason, when the selected engine does not stream. A render that fails midway ends its
  sentence and speech goes on with the next. Engine protocol stays 2: `stream` is optional in the manifest and in the
  `render` request, and an engine that ignores it is still correct. `orb doctor` says `streams` on the `tts engine` line
  when both are on ([#322](https://github.com/NerdoBaggins/agent-orb/issues/322)).
- `orb open` returns once the window is open, without waiting for the speech engines. The window works in text and says
  what each engine is doing while it loads, speech turns on when they finish, and an engine that fails to load
  leaves the window open with the reason. A load is timed by silence, 30 s between the lines an engine writes, so a
  first-run download that keeps reporting is no longer cut off after 5 minutes
  ([#203](https://github.com/NerdoBaggins/agent-orb/issues/203)).
- The orbs on a machine now share one set of speech-to-text and voice engines, where each used to start its own (two
  orbs with a large speech-to-text model held two copies of it in memory). Orbs share an engine when its name, load
  settings and declared environment variables match; voice, language and speed travel with each sentence, so changing
  them in one orb touches no other. An engine stays up while any orb uses it and closes 120 s after the last one
  goes, whether that orb closed or died, and the engine host exits 10 s after its last engine. `orb engines` lists
  what is running and which orbs use it, `orb engines stop` closes the host and every engine, `orb doctor` checks the
  host and every engine's protocol, and an engine's `engine.json` can say `"share": false` to get one process per orb
  ([#203](https://github.com/NerdoBaggins/agent-orb/issues/203)).
- Server capture: with `"capture": {"engine": "livekit"}` in your config, the built-in `livekit` engine records the
  microphone instead of the browser, with a 500 ms buffer and WebRTC noise suppression, so a Remote Desktop microphone loses no
  audio. `orb doctor` checks how much of the microphone's audio arrives
  ([#196](https://github.com/NerdoBaggins/agent-orb/issues/196)).
- `orb command <name> [argument]`, and `orb.command(name, argument)` for panels and widgets, run a built-in Claude Code
  command such as `/compact keep the open questions` in the attached session. The orb types it into the session's
  terminal once its turn ends and checks the transcript that it ran. Only the commands in your new `commands` config
  list run, `compact` and `context` unless you add more
  ([#181](https://github.com/NerdoBaggins/agent-orb/issues/181)).
- `orb command compact` and `orb.command("compact", …)` run under Copilot CLI too, so a widget's compact button
  compacts a Copilot session: the plugin's extension compacts the session once the turn in progress ends
  ([#221](https://github.com/NerdoBaggins/agent-orb/issues/221), [#225](https://github.com/NerdoBaggins/agent-orb/issues/225)).

### Changed

- Engines now speak protocol 2, with no way back: `engine.json` needs `"protocol": 2`, an engine's `load` reply carries
  its own `pid`, `render` takes `voice` and `lang` with each call, and `voices` lists the default voice first and no
  longer has `current` or a `voice` op. An engine without `"protocol": 2` is refused with a message naming its file; the
  orb uses the built-in default instead (a refused wake or capture engine turns that feature off) and `orb doctor` names
  it. Kokoro's voice setting is now `"default"`, the
  first entry in its voice list, so the voice menu marks it.
- Every engine now exits within 5 s of its standard input closing, even in the middle of a call, and whatever started it kills one
  that does not, by the `pid` it reported.
- A leftover `engines` key in your config is now ignored with the usual unknown-key warning.

### Fixed

- An engine's `max_seconds` must now be a finite number of at least 5 seconds, the length of the window the orb
  searches for a quiet point to cut a long clip at; anything else is refused when the engine loads, with its
  `engine.json` named. A value under 5 could make transcription never return, and infinity crashed it
  ([#315](https://github.com/NerdoBaggins/agent-orb/issues/315)).
- A feed's `every` and `tts.speed`, one number or a table per agent, must now be finite numbers: NaN, infinity
  or an integer too large for a float is refused at load with the file named. NaN and infinity used to make a feed
  never run and were clamped as a speed. A speed or size from the window that large no longer closes its socket
  ([#299](https://github.com/NerdoBaggins/agent-orb/issues/299)).
- A speech engine no longer outlives the orb that started it and keeps using the processor: engines now notice their
  input closing while they are loading or answering a call, and end within 5 s
  ([#165](https://github.com/NerdoBaggins/agent-orb/issues/165)).
- The mic button starts on a mouse press even when Chrome sends no pointer event for it, which had left every click
  ignored while the talk key still worked ([#249](https://github.com/NerdoBaggins/agent-orb/issues/249)).
- On Windows, `orb command` and the orb's Stop keep reaching a Claude Code session after Claude Code updates itself
  while the session runs, instead of refusing it as "not a Claude Code process"
  ([#219](https://github.com/NerdoBaggins/agent-orb/issues/219)).
- The transcript shows the newest turns when you come back to it from another panel's tab or the orb-only view,
  instead of staying where it was until the next turn arrives; if you had scrolled up, it stays where you left it
  ([#240](https://github.com/NerdoBaggins/agent-orb/issues/240)).
- A reply's numbered list no longer has its numbers cut off at its left edge, which happened past item nine and,
  with a monospace theme font, from item one. Lists in replies, bulleted ones too, are indented a little deeper
  ([#239](https://github.com/NerdoBaggins/agent-orb/issues/239)).
- The Copilot transcript no longer shows only the last few turns: the orb reads the last 16 MB of a session's log when
  it attaches and then keeps its newest 4000 events, so one compaction or a few large events no longer push the
  conversation out of view; 4000 events that are not messages still do
  ([#231](https://github.com/NerdoBaggins/agent-orb/issues/231), [#236](https://github.com/NerdoBaggins/agent-orb/issues/236)).
- Stop while the orb is transcribing now drops what you said, instead of sending it to the agent once transcription
  ends ([#132](https://github.com/NerdoBaggins/agent-orb/issues/132)).
- When a push-to-talk or wake-word clip arrives with stretches of lost audio, the orb now says so ("your mic audio
  is arriving with gaps (50% of it lost)") instead of "didn't catch that". Chrome loses audio this way from a
  Remote Desktop microphone ([#195](https://github.com/NerdoBaggins/agent-orb/issues/195)).
- The agent now runs the orb as `python -m orb`, so Windows Smart App Control no longer blocks every orb
  command ("An Application Control policy has blocked this file"). The `orb` command still works where nothing
  blocks it.
- With Copilot 1.0.105 or later, when Copilot gives up on a turn because it can't get a response from its model, the
  orb now ends the request and shows Copilot's reason. It no longer stays thinking for up to 30 minutes and shows the
  session as busy until the next prompt ([#184](https://github.com/NerdoBaggins/agent-orb/issues/184)).

## 0.6.0 — 2026-11-03

### Upgrading

- `panel_dirs`, `skin_dirs`, `theme_dirs` and `widget_dirs` now add up across your config files, so a folder one file
  lists stays searched when another file lists more. Your file for one repo
  (`~/.config/orb/repos/<repo id>/config.json`) can no longer narrow or clear the list in your file for every repo
  (`~/.config/orb/config.json`): a folder listed there is searched in every repo, and removing it there removes it from
  all of them. If you relied on a later file replacing an earlier one's list, remove the folders you no longer want from
  the file that lists them.
- `settings` is now a built-in panel name. Rename the folder of any saved panel called `settings`: it is listed struck
  through and cannot be opened. A page shown with `--name settings` now opens the built-in settings panel instead, so
  give it another name.

### Added

- A `panels` menu in the bar lists every panel you can open, sorted by title: the built-in transcript, release notes and
  settings, and your saved panels. Each shows where it comes from (built in, yours for this repo, a custom folder, or
  global) and its folder, with an `open folder` button; a panel hidden by an earlier one of the same name is struck
  through ([#139](https://github.com/NerdoBaggins/agent-orb/issues/139)).
- A `settings` panel, opened from a bar button or the panels menu, for what you set once: collapsible sections for the
  panel, skin, theme and widget folders. Add a folder by typing the path or choosing it with `Browse…` (the system's
  folder dialog), remove one with an `Undo` that puts it back in its old place, and click a section's speaker for the
  agent to explain it. A section with a missing folder opens itself, and which sections you leave open are remembered
  ([#139](https://github.com/NerdoBaggins/agent-orb/issues/139)).

### Changed

- The four folder lists add up across config files, as described under Upgrading
  ([#139](https://github.com/NerdoBaggins/agent-orb/issues/139)).
- `settings` is a built-in panel name, as described under Upgrading
  ([#139](https://github.com/NerdoBaggins/agent-orb/issues/139)).

### Fixed

- The orb skill now says an `.orb-speaker` button holds a word and never an icon, so a panel an agent writes no longer
  shows two speakers on a button ([#153](https://github.com/NerdoBaggins/agent-orb/issues/153)).

## 0.5.0 — 2026-10-31

### Added

- A release notes panel: press `R` or use the bar's `release notes` button to see what changed in each release. Click
  the speaker beside a change or a release to hear it explained (it becomes a stop button until it is done, and the other speakers wait), and
  `A−` or `A+` to make the text smaller or larger
  ([#114](https://github.com/NerdoBaggins/agent-orb/issues/114)).
- Type a request without leaving the orb: `Enter` or the bar's `type` button opens a box under the caption. `Enter`
  sends, `Shift+Enter` starts a new line, and `Escape` closes it and keeps the draft. What you type reaches the agent
  as your own words, waits its turn, and is answered aloud. While the box is open a panel can't take the focus from
  it: one that keeps trying is closed with a notice, and a widget is turned off
  ([#120](https://github.com/NerdoBaggins/agent-orb/issues/120)).

### Changed

- The orb reaches Copilot through the plugin's own Copilot extension, behind a per-start secret like the Claude Code
  channel's, instead of through `copilot --ui-server`, whose port takes no credential. Start Copilot with
  `copilot --experimental`, or run `/experimental on` in Copilot once: Copilot loads extensions only in experimental
  mode. The `copilot_port` setting and `ORB_COPILOT_PORT` are gone: drop them, and `--ui-server`, from any shell
  function that starts Copilot for the orb ([#111](https://github.com/NerdoBaggins/agent-orb/issues/111)).

### Fixed

- A quick tap on the talk key or the mic button on the first use of a session no longer leaves the mic recording
  ([#123](https://github.com/NerdoBaggins/agent-orb/issues/123)).
- An engine's warnings are now in the server log that `orb log` prints, as plain text, instead of raw in the output
  file: ASCII text that an engine writes as UTF-16 or with colour codes, such as the Kokoro engine's GPU warnings,
  no longer comes out garbled ([#66](https://github.com/NerdoBaggins/agent-orb/issues/66)).

## 0.4.0 — 2026-10-30

### Changed

- The orb opens only in Chrome (Chromium on Linux); it no longer falls back to Microsoft Edge, which was never
  verified ([#97](https://github.com/NerdoBaggins/agent-orb/issues/97)).

### Fixed

- A widget can be moved by any of its edges, not only the top one, so one placed under the bar can be moved again;
  the side and bottom edges are invisible until hovered
  ([#93](https://github.com/NerdoBaggins/agent-orb/issues/93), [#95](https://github.com/NerdoBaggins/agent-orb/issues/95)).
- A 2D skin, such as `pulse`, is no longer drawn stretched when the orb opens with a panel already showing
  ([#91](https://github.com/NerdoBaggins/agent-orb/pull/91)).

## 0.3.0 — 2026-10-29

### Added

- Each entry of the session's feed in the transcript shows the local time the session log gives it (`HH:MM:SS`),
  with a date line before the first timed entry and wherever the day changes; a time-zone change redraws them
  ([#57](https://github.com/NerdoBaggins/agent-orb/pull/57)).

### Changed

- The voice speed slider in the toolbar is labelled "speed"
  ([#80](https://github.com/NerdoBaggins/agent-orb/pull/80)).

### Fixed

- A request spoken or sent while the orb was reading out a reply to a wake-word request is sent once the
  follow-up window ends; on Windows the timer could fire a tick early and leave it waiting
  ([#82](https://github.com/NerdoBaggins/agent-orb/pull/82)).

## 0.2.0 — 2026-10-28

### Added

- The Kokoro voice renders on an NVIDIA GPU when its CUDA libraries load, and on the CPU otherwise
  ([#61](https://github.com/NerdoBaggins/agent-orb/pull/61)).
- Skin and theme folders added or removed while the orb is open reach the bar's pickers on the next page poll,
  without reloading the page ([#41](https://github.com/NerdoBaggins/agent-orb/pull/41)).
- Several Copilot sessions can each have an orb: the orb dials `ORB_COPILOT_PORT` when the shell that started
  Copilot set it, else `copilot_port` ([#31](https://github.com/NerdoBaggins/agent-orb/pull/31),
  [#32](https://github.com/NerdoBaggins/agent-orb/pull/32)).

### Changed

- The open toolbar is a narrow column at the right edge, wrapping into a second column in a short window, and
  its dropdown lists use the theme's colors ([#77](https://github.com/NerdoBaggins/agent-orb/pull/77)).
- The hint for starting Copilot names only what the orb needs, `copilot --ui-server --port N`, without `--yolo`
  or `--continue` ([#34](https://github.com/NerdoBaggins/agent-orb/pull/34)).
- A panel is capped at 1,000 files, 255-character paths and a 200-character title, besides the 8 MB and
  64-character name it already had; `orb panel show` refuses one over a cap and names it, except that a one-off
  page's name not given with `--name` is shortened to 64 characters and a title not given with `--title` to 200
  ([#36](https://github.com/NerdoBaggins/agent-orb/pull/36)).
- The README lists what the orb does up front, follows a newcomer's path from first run to uninstall, and says
  speech stays local with the built-in engines while an engine you add may send audio or text to a service
  ([#29](https://github.com/NerdoBaggins/agent-orb/pull/29),
  [#30](https://github.com/NerdoBaggins/agent-orb/pull/30)).

### Fixed

- On Windows the orb server starts with a hidden console, so there is no console window whose closing ends the
  orb ([#59](https://github.com/NerdoBaggins/agent-orb/pull/59)).
- Releasing a dragged toolbar slider away from the bar no longer leaves its controls taking space while
  invisible ([#77](https://github.com/NerdoBaggins/agent-orb/pull/77)).
- A skins, themes or panels folder that cannot be listed, or a widgets folder that cannot be reached, no longer
  crashes the orb: it is reported by name, a
  chosen skin or theme in it falls back to the default saying why, and `orb doctor` fails naming an unlistable
  skins or themes folder. A saved panel or widget that is a link the orb cannot follow is reported by name
  ([#37](https://github.com/NerdoBaggins/agent-orb/pull/37)).
- Panels with between about 750 KB and 8 MB of files show instead of failing at the server's 1 MB body limit
  ([#36](https://github.com/NerdoBaggins/agent-orb/pull/36)).
- A blank `orb.send` from a panel is refused with a reason instead of leaving its promise pending forever
  ([#35](https://github.com/NerdoBaggins/agent-orb/pull/35)).
- Requests reach the agent in the order they were made: a typed, panel or spoken request is queued before its
  wait notice goes out, so a later one cannot overtake it
  ([#39](https://github.com/NerdoBaggins/agent-orb/pull/39)).

## 0.1.0 — 2026-10-27

The first release: a local voice window for Claude Code and GitHub Copilot CLI.

- Push to talk, with the button or Space, and the reply read aloud.
- Local transcription and voice with the built-in engines; your own speech engines plug in as folders.
- Voice, engine and speed chosen from the bar and saved per repo; `M` mutes, `Escape` stops.
- Status, repeat and "what did I miss?" answered while the agent works; other requests wait for the turn to end.
- A transcript of every exchange, typed or spoken, with the agent's steps folded in.
- Panels the agent writes, shown beside the orb with tabs, explain buttons and live data from your own feeds;
  actions for buttons that change something without the agent.
- Widgets floating over the orb, skins and themes, and an optional wake word that can answer only your voice.
