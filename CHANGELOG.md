# Changelog

Every change a user of agent-orb can notice; a pull request no user would notice has no entry. Each pull request
that has one adds it under Unreleased.

## Unreleased

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
