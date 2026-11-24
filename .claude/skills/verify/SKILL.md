---
name: verify
description: Drive a real orb end to end in headless Chromium and report, flow by flow, whether a person could use it: hold-to-talk by mouse and key, typed requests, panels, mute. Speech and the Claude Code session are stubbed; the server, page, engine protocol and the plugin's hooks are real. Use when Alberto asks to "verify the orb" or "run the e2e", locally or in a cloud session, where it needs no microphone, GPU or speech model. It runs only when asked.
---

# Verify the orb

## Run it

The skill that judges a change is `main`'s copy. The branch's own copy is used only until the skill reaches `main`, and
the command says so when it does:

```bash
git fetch -q origin main
gate="$(mktemp -d)"
if git cat-file -e origin/main:.claude/skills/verify/verify.py 2>/dev/null; then
  git archive origin/main .claude/skills/verify | tar -x -C "$gate"
else
  echo "verify is not on main yet: this run uses the branch's own copy"
  mkdir -p "$gate/.claude/skills" && cp -R .claude/skills/verify "$gate/.claude/skills/"
fi
uv run python "$gate/.claude/skills/verify/verify.py" --subject . --changed-since origin/main
```

The stubs it drives (the `fake` engines, `hooks/hooks.json`) still come from the branch.

`--list` prints the flows. `--changed-since` adds the `checklist.json` items whose paths the change touches: their
flows join the run, and their checks are printed for you to carry out and report.

## Reading the result

Open the failing flow's screenshot and `orb.log` in the evidence folder before changing code. After three rounds that
still fail, stop and bring Alberto the failing flows and the evidence folder. Never edit a flow to make it pass.

Report each `shortcut:` line with the result.

## Checking the checker

`--benchmark` replays `benchmark.json`: each flow must fail on the commit before its fix and pass on the fix. Run it
whenever `verify.py`, a flow, or the stubs change, and report every case that is not `caught`; `harness.log` beside
each case's evidence says why. It checks out old commits, so a shallow clone needs `git fetch --unshallow` first.

When a bug escapes verify, add the flow that would have caught it, its fix to the benchmark, and a checklist item when
the risk belongs to particular files.

## Adding a flow

A change to something a person does in the orb changes its flow in the same change. Decorate an async function with
`@flow`, `@cold_flow` (a window whose microphone has never opened) or `@extra_flow` (on demand). Assert on what the
server and the session saw, and show the flow fails on the commit before the feature.
