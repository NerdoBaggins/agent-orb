---
name: verify
description: Drive a real orb end to end in headless Chromium and report, flow by flow, whether a person could use it: hold-to-talk by mouse and key, typed requests, panels, mute. Speech and the Claude Code session are stubbed; the server, page, engine protocol and the plugin's hooks are real. Use when Alberto asks to "verify the orb" or "run the e2e", locally or in a cloud session, where it needs no microphone, GPU or speech model. It runs only when asked.
---

# Verify the orb

`verify.py` starts the checkout's own orb server with the built-in `fake` speech engines, opens its page in headless
Chromium with a fake microphone, and answers every request through a stand-in session that writes a Claude Code
transcript and runs the plugin's hooks from `hooks/hooks.json`. Each flow ends with a screenshot; the run leaves a
Playwright trace, the page's console, the server log and `report.json` in one folder.

`--list` prints the feature map: every flow and what it proves.

## Run it

The harness, checklist and benchmark that judge a change are the ones on `main`, never the branch's own copies, so a
change cannot loosen the checks that grade it:

```bash
git fetch -q origin main
gate="$(mktemp -d)"
git archive origin/main .claude/skills/verify | tar -x -C "$gate"
uv run python "$gate/.claude/skills/verify/verify.py" --subject . --changed-since origin/main
```

`--changed-since` also runs `checklist.json`: each item names the paths that trigger it, the past fixes behind it, a
check, and any on-demand flows it needs. A triggered item's flows join the run; its check is printed for you to carry
out and report. Items fire only on a matching diff, so nobody has to remember past breakages.

When the branch edits the skill itself (a new flow for a new feature), run the branch's copy as well and report the
two results separately; only `main`'s is the gate. Until `verify.py` exists on `main`, say that the run used the
branch's copy.

First run on a machine: `uv run playwright install chromium-headless-shell` (on Linux add `--with-deps` if Chromium
reports missing libraries).

## Reading the result

- Exit 0 and `N/N flows passed`: report it with the evidence folder the run printed.
- A `FAIL` line names what it waited for; the page's notice is appended when it showed one. Open the screenshot and
  `orb.log` in the evidence folder before changing code.
- Fix and run again. After three rounds that still fail, stop and bring Alberto the failing flows and the evidence
  folder rather than a fourth guess.
- Never edit a flow to make it pass. A flow that is wrong is fixed on its own branch, reviewed, and merged first.

## Shortcuts

A flow that acts through a page script instead of as a person would says so in its `shortcut`, and every run prints
it under the flow. Report shortcuts with the result; a pass through a shortcut proves less than a pass through a click.

## Checking the checker

`--benchmark` replays `benchmark.json`: for each past fix, the named flow must fail on the commit before the fix and
pass on the fix. Run it whenever `verify.py`, a flow, or the stubs it drives change, and report any case that is not
`caught`. It checks out old commits, so a shallow clone needs `git fetch --unshallow` first. When a bug escapes verify, add the flow that would have caught it and the fix to the benchmark, and a
checklist item if the risk belongs to particular files.

## Adding a flow

A change that adds or alters something a person does in the orb adds or alters its flow in the same change: an async
function decorated with `@flow("what a person does, and what must follow")`. Assert on what the server and the session
saw, not only on the DOM; `round_trip` already proves a request reached the session and its reply came back through
the Stop hook. Show the flow can fail: run it against the commit before the feature and watch it go red. `@cold_flow` gives a flow a
window whose microphone has never opened; `@extra_flow` makes one run only when named or a checklist item calls for it.
Prune: an item or flow whose risk is gone is deleted, not kept for luck.
