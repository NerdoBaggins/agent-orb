# Intent: Widgets — the user's own boxes on the orb window
Author: Alberto Gonda. Status: approved (2026-10-22). Issue: #40.

Amends `specs/customization/intent.md`: adds a third drop-in kind beside skins and themes.

## Problem
Everything on the orb window itself is the plugin's. A user can change how the orb is drawn (a skin) and how the
window is styled (a theme), and the agent can open a panel beside it, but nobody can put a small piece of their
own on the orb's side — a meter, a clock, a count — without editing agent-orb. A panel is the wrong shape for
that: it is a tab the agent opens, it takes the window's other half, and it is gone when the orb is alone.

The case that raised it: a context meter for the Claude Code session, a bar with used and left, visible beside the
microphone the whole time, fed by numbers Claude Code already hands the user's status line.

## Proposed outcome
- **A widget is a folder the user drops in**, like a skin or a theme: a small page plus a manifest naming it, its
  starting size and corner, and what data it expects. Installed widgets appear in the bar's menu; the user ticks
  one on and it floats over the orb, in the orb's side of the window, dragged to wherever they want and resized.
  The place and size are remembered per repo.
- **A widget is a page, never code the orb runs.** Its data comes the way a panel's does: from the user's own feed
  in their feeds folder, run by the orb while the widget is on. Buttons that change something go through the user's
  own actions, as a panel's do. A widget from a repo brings a page and nothing that executes on the host.
- **The first widget, the context meter**, is the user's own, to be built and specified in the user's own repo; this
  feature is only the mechanism that lets it exist.

## Affected users and systems
- The author first, then anyone using the orb, in any repo.
- The orb page (`web/`), the server's panel plumbing (serving, feeds, actions, reload), config (two keys), the
  CLI (`orb widgets list`, `orb widget on|off`), `orb doctor`, the skill's description of panels.
- Panels, skins and themes keep working unchanged.

## Constraints
- Nothing personal is committed; a widget turned on, its place and size are per repo in the user's home folder.
- A repo ships no widget: a widget comes only from the user's folders, and a repo's `.orb/widgets/` is not loaded.
- Widgets are sandboxed exactly as panels are and reach the session only through `orb.send`.
- The plugin ships no widget of its own (spec, Decisions 1).
- Windows and macOS, Claude Code and Copilot CLI, as before.

## Open questions
All four were ruled on 2026-10-22 (spec, Decisions): the meter lives in the user's own repo; one name space with
panels; off until turned on; the feed's environment names the repo, agent and session.
