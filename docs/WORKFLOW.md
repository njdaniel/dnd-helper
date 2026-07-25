# Workflow

Solo project, but written down so that coding agents and future-you follow the
same rules.

---

## The loop

1. Pick an issue whose blockers are all closed and which is not labelled
   `blocked`. Prefer the lowest-numbered task in the current phase milestone.
2. Branch from `main`: `phase-1/1.2-speech-layer` (`<phase>/<task>-<slug>`).
3. Build it. One issue per branch — resist scope creep into the next issue.
4. Open a PR with `Closes #N`. CI must be green.
5. **Review it** — see below. Agent-written PRs get an independent pass.
6. Perform the issue's **manual verification** steps and report the result in a
   PR comment. An unverified PR is not done.
7. Squash-merge. Delete the branch.

At the end of a phase, stop. Run the epic's acceptance gate before starting the
next phase.

## Branch names

```
phase-0/0.2-database-layer
phase-1/1.1b-ollama-provider
fix/webhook-cache-eviction
docs/architecture-provider-seam
```

## Commits

Describe behavior, not files. `git log` should read like a changelog.

```
good:  npc replies now split on paragraph boundaries over 1900 chars
       router ignores messages starting with (( as out-of-character
bad:   update speech.py
       fix bug
       wip
```

## Pull requests

The template encodes the definition of done from `CLAUDE.md`. The parts that
get skipped in practice and shouldn't be:

- **No secrets in the diff.** This repo is public. Scan the diff, not just your
  intent.
- **`.env.example` updated** if you added config.
- **A test for what you built.** Not "tests pass" — a new test.
- **Manual verification actually performed**, with the result written down.

## Review

`main` is protected: CI must be green, the branch must be up to date, history
stays linear, and conversations must be resolved before merge. Force-pushes and
deletion are blocked.

**What protection cannot do is tell you the design is wrong.** These rules exist
because CI passing is a weak signal on agent-written code — every defect found
so far had a green suite behind it.

**1. The reviewer is not the author.** An agent does not review its own work,
and neither does the process that wrote the issue. For agent-written PRs, run
an independent pass before merging:

```bash
codex review          # in the PR's worktree
```

That matters more than it sounds. Whoever wrote the issue shares its blind
spots — code that faithfully implements a wrong spec reads as correct to them.

**2. Run the gate yourself, outside the agent's sandbox.** Agents have
repeatedly reported "all checks passed" while structurally unable to run the
test suite — no network for `pip install`, no writable `.git`. Treat an agent's
green as unverified until you have reproduced it.

**3. Check the acceptance criteria one at a time**, against the issue. Not "do
the tests pass" — tests pass on code that satisfies none of them.

**4. Look specifically for these**, because they have all actually happened here:

- Work that satisfies the letter of the scope by violating a convention in
  `CLAUDE.md` — usually because the scope was too narrow
- A seam left unwired: a provider, cog, or handler written but never registered
- Config read from the wrong place (`alembic.ini` vs `DATABASE_URL`)
- Tests that assert on a fixture rather than on real behaviour
- Dead code shaped like a safety check
- A weakened test — especially `test_secrets_barrier.py`. **If it fails, the
  code is wrong.**

**5. `needs-human` PRs do not merge on CI alone.** If the issue says "in a test
server," someone runs it in a test server first.

**Solo-repo caveat:** GitHub will not let you approve your own pull request, so
required approvals are not enforced — they would deadlock a one-person repo.
Review here is a discipline backed by an independent tool pass, not a gate the
platform imposes. That is a real weakness; know it rather than assume the
branch rules cover it.

## Labels

| Label | Meaning |
|---|---|
| `phase:0` … `phase:4` | Which phase it belongs to |
| `epic` | Phase container; holds tasks as sub-issues, carries the gate |
| `type:feat` `type:infra` `type:docs` `type:test` | Kind of work |
| `area:discord` `area:llm` `area:db` `area:memory` `area:commands` `area:prompt` | Subsystem |
| `agent-ready` | Spec is complete enough to hand to an agent unattended |
| `needs-human` | Requires a token, a live server, or a taste judgment |
| `blocked` | Has an open dependency; do not start |
| `on-hold` | Phase 4 — deliberately not started |

## Dependencies

Every task issue states its blockers as `Blocked by #N` in the body and carries
the `blocked` label until they close.

**The label maintains itself.** `.github/workflows/unblock.yml` runs
`scripts/unblock.py` whenever an issue closes or reopens (plus daily, as a
safety net). It parses every open issue's `## Blocked by` section, removes
`blocked` once all dependencies are closed, and re-adds it if a dependency is
reopened. Run it locally any time:

```bash
python3 scripts/unblock.py --dry-run    # report, change nothing
python3 scripts/unblock.py              # reconcile
```

The body text is the source of truth; the label is derived. So when
dependencies change, **edit the `## Blocked by` section** and let the script
sort out the label — don't hand-edit labels and expect them to stick.

Most of the graph is a chain. The places where work can genuinely run in
parallel:

- **After 0.3:** `1.1`, `1.2`, and `1.3` are independent
- **After 0.2:** `2.1` (lore CRUD) can be built any time, alongside Phase 1
- **After 1.5:** all of Phase 3 opens at once

Those are the moments to fan out multiple agents. Everything else, run in order.

---

## Where questions live

Three places, with a hard line between them. The line is *does this block
work* — not how interesting it is.

| Where | For | Ends when |
|---|---|---|
| **Issue + `decision` label** | An open question that blocks work and needs an outcome | You pick one → write the ADR → close the issue |
| **[Discussions](../../../discussions)** | Open-ended, no deadline — "should we ever…", "has anyone tried…" | Maybe never. That's fine. |
| **`docs/DECISIONS.md`** | The settled record: what was chosen and why | It's already the end |

Decisions are **issues, not Discussions**, because an issue can block a task.
`#7 blocked by #33` shows up in the dependency graph and stops an agent from
building the wrong thing. A Discussion can't do that.

The `.github/ISSUE_TEMPLATE/decision.yml` template asks for context, at least
two options with their costs, reversibility, and a recommendation. If you can't
name two options, it isn't a decision — just do the thing.

**Reversibility is the field people skip and shouldn't.** Cheap-to-reverse
decisions should be made fast and moved past; expensive ones deserve the
argument. Storage layout (#33) is expensive *now* and nearly free to argue,
which is exactly why it's open before #7 rather than after.

Closing a decision issue means writing the ADR. An issue closed without one
leaves the next person — or the next agent — with no record of why.

---

## The orchestrated loop

How an issue actually becomes merged code. Written down because the *rules*
above are easy to reconstruct and this sequence is not.

```
handoff.py --list          ← what is genuinely ready
      ↓
git worktree + venv        ← isolation, dependencies pre-installed
      ↓
handoff.py <n>             ← prompt built from the live issue + CLAUDE.md
      ↓
codex exec -s workspace-write -C <worktree>
      ↓
   ┌──┴──────────────┐
   │ agent hits a    │ → fix the ISSUE, regenerate the prompt, rerun
   │ spec conflict   │
   └──┬──────────────┘
      ↓
run the gate OUTSIDE the sandbox     ← the agent structurally cannot
      ↓
review against the acceptance criteria, one at a time
      ↓
commit + open the PR                 ← the agent cannot
      ↓
codex review --base main             ← independent pass
      ↓
branch protection: CI, up to date, linear, conversations resolved
      ↓
human verification for needs-human
      ↓
merge → unblock.yml runs → the next issues become ready
```

### Who does what, and why it is not a preference

| Step | Who | Why not the agent |
|---|---|---|
| Write the code | agent | — |
| Run the tests | you | The sandbox has no network, so `pip install` fails, and `aiosqlite` hangs there even with a pre-built venv |
| Commit | you | A worktree's `.git` lives outside the sandbox and is read-only |
| Independent review | `codex review` | Whoever wrote the spec shares its blind spots |
| "Does it work in Discord / sound like a person" | you | Taste, and a real token |

**Treat an agent's "all checks passed" as unverified.** It has repeatedly been
truthful about ruff and mypy while structurally unable to execute pytest. That
is not dishonesty; it is the sandbox. Reproduce the gate yourself before
believing it.

### Setting up a run

```bash
python3 scripts/handoff.py <n> > /tmp/handoff-<n>.txt
BR=$(grep -m1 '^Branch: ' /tmp/handoff-<n>.txt | cut -d' ' -f2)   # use the derived name
git worktree add ../dnd-helper-wt-<n> -b "$BR"
cd ../dnd-helper-wt-<n> && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
codex exec -s workspace-write -C "$PWD" "$(cat /tmp/handoff-<n>.txt)"
```

Take the branch name from `handoff.py` rather than inventing one, or the
in-flight guard in `--list` will not match it and the issue will be offered to
a second agent.

Append to the prompt: where the venv is, that there is no network, and that a
pytest hang on `aiosqlite` is the environment rather than their bug — otherwise
an agent may "fix" a test that was never broken.

### The two feedback loops that carry the weight

**A spec conflict is fixed on the issue, never in the code.** When an agent
stops and says the scope forbids something it needs, edit the GitHub issue,
then regenerate the prompt. `handoff.py` reads live, so the correction
propagates. Patching around it in code leaves the next agent at the same wall
and the issue still wrong.

**Review is separated from authorship.** Run `codex review --base main` in the
PR's worktree. It has found defects that were looked at directly and cleared.

### What this has actually caught

Eleven defects after CI was green — six from review, five from the independent
pass, **none from CI**. Seven of the eleven were gaps in the *issue*, not the
implementation: the agents built what was asked for.

The lesson worth carrying: **issue quality is the bottleneck, not agent
capability.** Time spent sharpening scope and acceptance criteria pays back
more than time spent reviewing output.

---

## Handing an issue to a coding agent

`AGENTS.md` symlinks to `CLAUDE.md`, so any agent that reads either gets the
same hard rules.

**Don't write the handoff by hand — generate it:**

```bash
python3 scripts/handoff.py --list     # what's ready right now
python3 scripts/handoff.py --next     # prompt for the next ready issue
python3 scripts/handoff.py 6          # prompt for a specific issue
```

The generated prompt pulls Scope, Acceptance criteria, and Gotchas live from
the issue, and the definition of done from `CLAUDE.md`. That matters more than
convenience: a hand-written handoff drifts from the issue, and the agent then
builds to your memory of the scope rather than the scope. It refuses epics,
decisions, and `on-hold` issues, and warns loudly if you hand off something
still labelled `blocked`.

Working directory hygiene when running more than one agent: **give each its own
git worktree.** Two agents editing the same checkout will clobber each other,
and the failure is confusing rather than loud.

```bash
git worktree add ../dnd-1.2 -b phase-1/1.2-speech-layer
git worktree add ../dnd-1.3 -b phase-1/1.3-npc-commands
```

Check `docs/ROADMAP.md` for which issues can genuinely run at once — most of
the graph is a chain, and parallelising a chain just produces merge conflicts.

Rules that exist specifically because agents get them wrong:

- **Don't build ahead.** An agent handed 1.1 will cheerfully implement 1.4 too.
  The issue scope is the scope.
- **Don't invent schema changes.** If the model needs a new column, stop and
  say so — migrations are a human decision.
- **Don't weaken a failing test to make it pass**, especially
  `test_secrets_barrier.py`. If it fails, the code is wrong, not the test.
- **Don't hardcode a model ID or a token.** Both come from config.
- **`agent-ready` is a claim about the spec, not the difficulty.** If the issue
  turns out to be underspecified, say so on the issue instead of guessing.

For issues labelled `needs-human`, an agent can still write the code — but the
acceptance step needs you, a real Discord server, and a real token.
