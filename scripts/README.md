# scripts/

## `handoff.py`

Generates a ready-to-paste prompt for handing a GitHub issue to a coding
agent (Codex, Claude, etc.), per the handoff format described in
[`docs/WORKFLOW.md`](../docs/WORKFLOW.md#handing-an-issue-to-a-coding-agent).

```
python3 scripts/handoff.py 6          # prompt for issue #6
python3 scripts/handoff.py --next     # find the next ready issue, then prompt for it
python3 scripts/handoff.py --list     # list all currently-ready issues, one per line
```

Requires the [`gh`](https://cli.github.com/) CLI, authenticated (`gh auth
login`) against this repo. No other dependencies — standard library only.

### What "ready" means

An issue is ready when it is open and carries none of these labels:
`blocked`, `on-hold`, `epic`, `decision`. `--next` picks the lowest-numbered
ready issue, matching "prefer the lowest-numbered task in the current phase
milestone" from `docs/WORKFLOW.md`.

### What the prompt contains

- An instruction to read `CLAUDE.md` and `docs/ARCHITECTURE.md` first
- The issue number, title, and a branch name derived from the title (see
  `docs/WORKFLOW.md#branch-names`)
- The issue's Scope, Acceptance criteria, and Gotchas sections, verbatim
- The "Definition of done for any task" from `CLAUDE.md`, read live so it
  can't drift out of sync with the actual rules
- A scope guard reiterating the rules agents get wrong: don't touch files
  outside scope, don't add dependencies unasked, don't build ahead, don't
  weaken a failing test
- A closing note if the issue's Manual verification section is non-trivial,
  so the agent doesn't claim completion before a human has run those steps

### Special cases

- **`blocked`** issues still print a prompt, but `handoff.py` prints a
  warning to stderr naming the blockers first — check they're actually
  closed before you paste the prompt anywhere.
- **`on-hold`** issues (Phase 4) and **`epic`**/**`decision`** issues (not
  implementation work) are refused outright, with an explanation, exit 1.
- Unknown issue numbers, or `gh` not being authenticated, also exit 1 with a
  message on stderr.
