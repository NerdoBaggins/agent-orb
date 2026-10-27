# Plan: Widgets

Author: Alberto Gonda. Status: built 2026-10-22 from the approved spec, on branch `widgets`. Spec: `specs/widgets/spec.md` (approved 2026-10-22). Issue: #40.

The ground rules of `specs/orb/plan.md` hold: each step ends in commits and a "done when" that is checked, not
assumed, and a finding that changes the spec goes back to the author before the build relies on it. This plan adds
one: `uv run pytest` is green at every step. One branch, `widgets`, one PR closing #40. The `context` widget is not built here (spec,
Decisions 1).

## 1. The shared folder shape

`orb/pages.py`: what a panel folder and a widget folder have in common, moved out of `panels.py`, not copied: the
name rule, the size cap, the manifest reader, the file walk, `signature`, `settled`, `collect_files`. `panels.py`
keeps its own manifest fields and `resolve_show`. `test_panels.py` keeps passing with no change beyond the home
of the size cap it patches, which is the proof nothing moved but the home.

Done when: `panels.py` holds nothing a widget also needs, and `test_panels.py` is green.

## 2. Widgets as folders

`orb/widgets.py`: `widget.json` validation (name, version, description, size, corner, defaults), discovery over
the four scopes (`widget_dirs` a folder key beside `panel_dirs`), `list_installed` with problems, `find`, and the
names a widget shares with a panel. `test_widgets.py` mirrors `test_panels.py`: scopes and shadowing, the invalid
folders each reported, the defaults, a bad corner, a size under the minimum.

Config: `widget_dirs` (defaults, types, folder keys, the layer-3 warning), `widgets` (dict, merge depth 2), each
entry's shape checked (`on` bool, `x`/`y` numbers in 0–1, `width`/`height` numbers) as a config error naming the
file. `test_config.py` gains the cases.

Done when: a widget in each scope lists with its mark, the narrowest wins, every invalid folder is reported by
name and reason, and a `widgets` entry of the wrong shape fails to load naming the file.

## 3. The server

- `self.widgets`, entries shaped like a panel's, served at `/w/{secret}/{name}/{path}` by the file handler the
  panels use, parameterised by the map; `index.html` gets the panel API.
- Feeds and reloads: `watch_pages` tends both maps, keyed `(kind, name)`; `tend_feed`, `feed_status`,
  `reload_page` take the kind and broadcast `panel_data` / `widget_data` and a `feed_status` carrying `kind`.
- On and off: `POST /widget {name, on}` (CLI) and the page message `widget {name, on}`; on resolves the folder,
  loads the files, writes layer 4, broadcasts `widget` with the saved place or the manifest's corner and size; off
  cancels the feed, writes layer 4, broadcasts `widget_close`. Page message `widget_place {name, x, y, width,
  height}` writes layer 4 and tells the other windows.
- At start, every `widgets` entry with `on: true` that is installed is loaded; one that is not installed is
  reported in the snapshot as named-but-missing.
- The snapshot carries `widgets: {installed: [...], shown: [...], missing: [...]}`.
- `page_action` and the page bridge find a frame in either map; the `action` message carries `kind`.
- `feeds.run` and `actions.run` take the extra environment: `ORB_REPO`, `ORB_AGENT`, and `ORB_SESSION` only when
  the link knows its session. `FakeLink` grows `session_id`.

`test_server_widgets.py`: served under the secret with the API, never in `self.panels`; on broadcasts `widget`,
off `widget_close` and cancels the feed; a feed's output reaches the page as `widget_data`; the three variables
reach a feed (a feed that writes its environment to its output); a panel and a widget of one name are tended
apart; a start with `on: true` shows the widget; a name on but not installed is reported, not raised.

Done when: those tests are green and `test_server_panels.py` is green, changed only where it calls `tend_feed`.

## 4. The page

- `web/widgets.js`, pure: `cornerPlace(corner, size, pane)`, `clampPlace(place, pane)`, `toPixels(place, pane)`,
  `fromPixels(rect, pane)`, `MIN_WIDTH`, `MIN_HEIGHT`; `web/test_widgets.mjs` under node; `orb/test_widgets_js.py`
  runs it.
- `orb.js`: `widgetsUi` (show, close, data, stale, place, clamp on resize and split change), frames inside
  `#orb-pane` between the canvas and `#controls`, edge as drag handle, grip as resize, `pointer-events: none` on
  the frame during its own drag, `widget_place` sent on pointer-up; the bridge finds a widget's frame; the bar's
  `widgets` pill and checklist modelled on the tasks list, greyed rows for named-but-missing.
- `orb.css`: `.widget`, `.widget-edge`, `.widget-grip`, the stale mark, the checklist.

Done when: a widget turned on from the bar appears at its corner, drags, resizes, stays inside the pane on a
window resize and on a split drag, hides in full-panel mode, and is back where it was after a reload.

## 5. CLI, doctor, docs

- `orb widgets list`, `orb widget on|off <name>` (through the server when one runs, else layer 4 for the next
  one, as `orb voice` does). `test_cli.py` gains the cases.
- `orb doctor`: invalid widget folders, a widget on but not installed, a `widget_dirs` entry that is not a folder;
  `test_doctor.py` gains a failing fixture for each.
- `skills/orb/SKILL.md` and `README.md`: a widgets section each, the four scopes, the shared name space, the two
  commands, what a widget's page gets.

Done when: the suite is green, `orb doctor` in a throwaway repo with a broken widget folder names it, and the
skill tells the agent how to write a widget's page and where the user's feed for it goes.

## 6. Ship

PR from `widgets` closing #40, the author's review, merge. Then the `context` widget in the user's own repo, under its own
spec there.
