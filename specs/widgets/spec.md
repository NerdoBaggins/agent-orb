# Spec: Widgets — the user's own boxes on the orb window

Author: Alberto Gonda. Status: approved (2026-10-22). Intent: `specs/widgets/intent.md`. Issue: #40.
Amends `specs/customization/spec.md`; where the two disagree, this one wins.

## Goal

A user adds a small box of their own to the orb window — on the orb's side, beside the microphone, not in a panel
tab — by dropping in a folder, turns it on from the bar, drags it where they want and resizes it, and the orb
remembers that for the repo. Nothing in agent-orb or on the machine is edited. The orb runs no code from the
folder: a widget's data comes from the user's own feed, as a panel's does.

## Non-goals

- A widget on a panel's side, or over the panels. Widgets live in the orb's pane and go with it.
- A widget as a tab, or full-window. That is a panel.
- A widget that reaches the server, the page or the session other than through the panel API.
- A plugin-shipped widget (Decisions 1).

## A widget is a folder

```
<name>/
  widget.json   {"name", "version", "description", "size": {"width": 160, "height": 48}, "corner": "top-left"}
  index.html    the page; files beside it are served, as a panel's are (no dotfiles, one level of subfolders, 1000 files, 255-character paths, 8 MB)
```

- `name`: the folder name, under the page name rule panels use (`orb/pages.py` `NAME`). `version`: a string.
- `description`: what the widget shows and what shape of `orb.data` it expects, so a user (or the agent) can write
  the feed for it.
- `size`: the starting size in CSS pixels; `corner`: one of `top-left`, `top-right`, `bottom-left`, `bottom-right`,
  the starting place. Both apply only until the user moves or resizes it. Left out: 160 × 48 and `top-left`.
- A widget's page gets what a panel's gets: the default panel stylesheet, the theme's tokens and `panels.css`,
  and `panel.js` (`orb.send`, `orb.speak`, `orb.stop`, `orb.action`, `orb.state`, `orb.data`, `orb.on`). Escape
  pressed in a widget is handed to the orb page as from a panel.
- Rules: sandboxed like a panel, with no origin; libraries from the internet are allowed as for a panel.

## Discovery

Mirrors panels, narrowest first; where a name exists in more than one place, the first wins:

| Scope | Folder |
|---|---|
| `mine` | `~/.config/orb/repos/<repo id>/widgets/<name>/` — the user's, this repo only |
| `custom` | each folder in the user's `widget_dirs` |
| `user` | `~/.config/orb/widgets/<name>/` — the user's, every repo |

`widget_dirs` is a config key like `panel_dirs`: a list of folders, absolute or `~/…`, read only from the user's
own two files (layer 3 warns and ignores it).

A folder whose `widget.json` is missing or invalid, or that has no `index.html`, is not a widget: `orb widgets
list` and `orb doctor` say which folder and why, and the window's menu never lists it.

## One name space with panels

A panel and a widget are two shapes of the same thing, a page under a name, so a name is one name across both.
A widget's feed lives where a panel's would, `~/.config/orb/feeds/<name>/` or the user's for this repo, and its
actions in `actions/<name>/`: one feed written for the context data serves a widget showing it small and a panel
showing it large. `orb widgets list` and `orb panels list` each mark a name that is installed as the other kind
too; nothing refuses it.

## Turning on, placing, sizing

- An installed widget is off until the user turns it on (Decisions 3). The bar gets a `widgets` entry, shown only
  while at least one widget is installed or named on but not shown: a checklist of installed widgets (name, scope
  mark, a tick), modelled on the tasks list, not a `<select>`. A name installed in more than one scope is one row. Ticking one on shows it at its starting corner and size; ticking it off hides
  it and keeps its place.
- A shown widget is a sandboxed frame inside a thin edge the orb page owns. The edge is the drag handle and shows
  in the accent on hover; its bottom-right corner is the resize grip. While a drag or resize is in progress the
  frame takes no pointer events (as a panel's does during a divider drag), so the page under the pointer cannot
  swallow it.
- Place is kept as fractions of the orb pane (the top-left corner at `x`, `y` in 0–1 of the pane's width and
  height); size in CSS pixels. A widget is clamped inside the pane on every move, resize, and window or split
  change, and never smaller than 48 × 24. There is no count limit; what fits, fits.
- Widgets sit above the orb canvas and below the controls (mic, stop, caption) and the bar, so the microphone is
  always reachable; a widget dragged under the controls is covered there.
- With panels open, widgets stay in the orb's pane and follow its width; in the full-panel mode they are hidden
  with the pane and return with it. This answers #40's question about full-screen panels.
- Saved per repo (layer 4) under `widgets`:

  ```json
  "widgets": {"context": {"on": true, "x": 0.02, "y": 0.04, "width": 180, "height": 44}}
  ```

  `x` and `y` come together, as do `width` and `height`; each pair may be absent. It merges per widget across the user's layers (`MERGE_DEPTH` 2, like `skin_options`); a repo's file may not set it. A widget named there but not installed
  is kept in the file, shown unticked and greyed in the menu, and reported by `orb doctor`.

## Data and actions

While a widget is on, the orb tends its feed exactly as a panel's: runs it when the output is older than `every`,
hands each new output to the page as `orb.data`, marks the frame's edge in the warn colour from a failure until new
data arrives, and says why once in the notice. Turning the widget off stops the feed, as closing a panel does. A
widget whose files change on disk is reloaded in place like a saved panel.

**The feed's environment gains three variables** (Decisions 4), for every feed and action, panel or widget:
`ORB_REPO` (the repo root), `ORB_AGENT` (`claude` or `copilot`) and `ORB_SESSION` (the attached session's id, set
only while the orb knows it). A feed that depends on the session reads `ORB_SESSION` instead of guessing from the
working directory.

## Commands

| Command | What |
|---|---|
| `orb widgets list` | installed widgets with scope marks (`[mine]`, `[custom]`, `[user]`), which are on, which names are also panels, and the folders that failed validation |
| `orb widget on <name>` / `orb widget off <name>` | what the bar's checklist does, for the agent when asked; saved per repo |

`orb doctor` reports widget folders that fail validation, a widget turned on that is not installed, and a
`widget_dirs` entry that is not a folder.

## Failure

- A widget page that fails to load, or is removed while on, shows its edge with the name and the reason on hover,
  and the orb says why once. A widget on in the config that cannot be shown at start is listed in the menu, greyed,
  with the reason. Nothing else on the window is affected.
- A widget's feed or action failure is the panel's: the warn mark, the reason once, the last data kept.
- The `widgets` config key with a wrong shape is a config error naming the file, as any other key.

## Checks

- `orb/widgets.py` validation, discovery and the shared name space: tests mirroring `test_panels.py`, built on the
  helpers panels and widgets share in `orb/pages.py` (`manifest`, `files`, `signature`, `settled`,
  `collect_files`), moved there from `panels.py` rather than copied.
- Config: `widget_dirs` under the folder-key rule, `widgets` typed and merged per widget, the layer-3 warning.
- Server: a widget is served under the panel secret and gets the panel API; turning one on broadcasts a `widget`
  message, not a `panel` one, and it never appears in `self.panels` (no tab); its feed is tended by the same code
  as a panel's; the three environment variables reach a feed; turning off cancels the feed.
- Page: the clamp and the fraction-to-pixel placement are a pure module (`web/widgets.js`) tested with node like
  `size.js`; the drag handle leaves the frame inert while dragging.
- CLI: `widgets list` marks scopes, on state and shared names; `widget on` refuses a name that is not installed.
- Doctor: the three checks above, each with a failing fixture.

## The first widget

The case that raised this feature, a context meter for the Claude Code session, is a widget and a feed of the
user's own: it lives in the user's own repo beside their skins and installs as a copy (Decisions 1). How it gets
its numbers is that widget's concern, specified there, not here. This spec ships no widget.

## Decisions (2026-10-22)

1. **Where the context meter lives.** Customization decisions 4 and 6 keep anything personal out of the plugin:
   personal skins are versioned in the user's own repo and installed as copies. The widget mechanism ships in
   agent-orb and closes #40; the `context` widget and its feed will live in the user's own repo
   (`projects/listener-core/agent-orb/`) and install the same way. #40's "first widget" paragraph read as if it
   were in agent-orb; it is not.
2. **One name space with panels**, as above, so one feed serves a name in either shape. The alternative, feeds
   keyed `widgets/<name>`, keeps the two apart at the cost of a second feed for the same data.
3. **Off until turned on.** Installing a widget does not show it; the bar's checklist does. The alternative, on
   when installed, means a widget appears the moment its folder does.
4. **`ORB_REPO`, `ORB_AGENT`, `ORB_SESSION` in every feed's and action's environment.** A small, general change
   to feeds; without it a session-bound feed can only guess.

A fifth question, how the context meter's numbers reach its feed, was struck: it belongs to that widget, in
the user's own repo, not to the mechanism.

## Amendment: a repo ships no widgets

The `repo` scope is gone. A widget in `<repo>/.orb/widgets/` is not loaded, listed or shown; `orb widgets list` names
the folder once so its absence is not a mystery. A widget comes only from the user's folders, so a cloned repo cannot
put a page beside the user's, replace one of theirs by name, or be handed their feeds. The repo's `.orb/config.json`
cannot turn a widget on or place it either: it sets only keys and `speak`.
