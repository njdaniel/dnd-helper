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
5. Perform the issue's **manual verification** steps and report the result in a
   PR comment. An unverified PR is not done.
6. Squash-merge. Delete the branch.

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
the `blocked` label until they close. When you close an issue, check what it
unblocked and drop the `blocked` label there.

Most of the graph is a chain. The places where work can genuinely run in
parallel:

- **After 0.3:** `1.1`, `1.2`, and `1.3` are independent
- **After 0.2:** `2.1` (lore CRUD) can be built any time, alongside Phase 1
- **After 1.5:** all of Phase 3 opens at once

Those are the moments to fan out multiple agents. Everything else, run in order.

---

## Handing an issue to a coding agent

`AGENTS.md` symlinks to `CLAUDE.md`, so any agent that reads either gets the
same hard rules. A good handoff is:

> Read `CLAUDE.md` and `docs/ARCHITECTURE.md`. Implement issue #N on a branch
> named `<branch>`. Follow the definition of done. Do not modify files outside
> the scope of that issue. Do not add dependencies without asking.

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
