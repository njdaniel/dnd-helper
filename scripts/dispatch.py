#!/usr/bin/env python3
"""Run `todo` issues through a coding agent, end to end.

For each issue picked up:

    todo -> in progress -> isolated worktree -> agent -> commit -> draft PR
         -> gate run OUTSIDE the agent's sandbox
         -> green: mark ready for review, attach an independent review
         -> red:   leave it draft, label needs-attention, say why

The gate runs here rather than inside the agent because the agent's sandbox has
no network (so `pip install` fails) and a read-only `.git` (so it cannot
commit). An agent reporting "all checks passed" is reporting on lint and types
only — see docs/WORKFLOW.md.

Usage:
    python3 scripts/dispatch.py --list          # what would be picked up
    python3 scripts/dispatch.py --dry-run       # plan without doing anything
    python3 scripts/dispatch.py                 # dispatch up to --max issues
    python3 scripts/dispatch.py --issue 48      # one specific issue
    python3 scripts/dispatch.py --max 3         # raise the concurrency cap
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = "njdaniel/dnd-helper"
HANDOFF = REPO_ROOT / "scripts" / "handoff.py"

TODO = "todo"
IN_PROGRESS = "in progress"
NEEDS_ATTENTION = "needs-attention"
SKIP_LABELS = {"blocked", "on-hold", "epic", "decision", IN_PROGRESS}

# Worktrees live beside the repo, not inside it: a nested worktree confuses
# ruff/pytest discovery and would land in the agent's own workspace.
WORKTREE_PARENT = REPO_ROOT.parent

# What the agent is told about its environment. Every line here exists because
# an agent got it wrong at least once.
ENV_PREAMBLE = """
## Environment
A virtualenv is already built at .venv with all dependencies installed. Use
.venv/bin/python, .venv/bin/pytest, .venv/bin/ruff, .venv/bin/mypy and
.venv/bin/alembic. Do NOT create a venv or run pip install — this sandbox has
no network access, and `gh` will not work either. Everything you need is in
this prompt.

pytest may hang on aiosqlite fixtures inside this sandbox. That is an
environment limitation, not your bug. If it hangs, say so and move on — do NOT
change repository code or weaken any test to work around it.

## When you are done
Leave your work uncommitted on the current branch. Committing will fail here
because the worktree's git metadata is outside the sandbox and read-only; that
is expected and is handled for you. Report what you changed, plus the actual
output of `.venv/bin/ruff check .` and `.venv/bin/mypy bot/engine bot/db`.
"""


class DispatchError(Exception):
    """A user-facing failure. Printed to stderr; does not stop other issues."""


# --------------------------------------------------------------------------
# shelling out
# --------------------------------------------------------------------------


def run(
    args: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 3600
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode != 0:
        raise DispatchError(
            f"{' '.join(args[:3])} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:400]}"
        )
    return result


def gh_json(args: list[str]) -> object:
    return json.loads(run(["gh", *args]).stdout)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


@dataclass
class Issue:
    number: int
    title: str
    labels: set[str]


def claimed_issues() -> set[int]:
    """Issues already covered by an open PR — including drafts."""
    import re

    prs = gh_json(["pr", "list", "--state", "open", "--json", "body", "--limit", "200"])
    pattern = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
    claimed: set[int] = set()
    for pr in prs:  # type: ignore[union-attr]
        claimed.update(int(n) for n in pattern.findall(pr.get("body") or ""))
    return claimed


def existing_branches() -> set[str]:
    out = run(["git", "branch", "--all", "--format=%(refname:short)"], cwd=REPO_ROOT)
    return {
        line.strip().removeprefix("origin/")
        for line in out.stdout.splitlines()
        if line.strip()
    }


def branch_for(issue: Issue) -> str:
    """Ask handoff.py, so the branch matches what its in-flight guard expects."""
    out = run(["python3", str(HANDOFF), str(issue.number)], cwd=REPO_ROOT)
    for line in out.stdout.splitlines():
        if line.startswith("Branch: "):
            return line.removeprefix("Branch: ").strip()
    raise DispatchError(f"no branch derived for #{issue.number}")


def ready_issues() -> list[Issue]:
    raw = gh_json(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            TODO,
            "--json",
            "number,title,labels",
            "--limit",
            "200",
        ]
    )
    issues = [
        Issue(i["number"], i["title"], {label["name"] for label in i["labels"]})
        for i in raw  # type: ignore[union-attr]
    ]
    claimed = claimed_issues()
    branches = existing_branches()
    out = []
    for issue in issues:
        if issue.labels & SKIP_LABELS:
            continue
        if issue.number in claimed:
            continue
        if branch_for(issue) in branches:
            continue
        out.append(issue)
    return sorted(out, key=lambda i: i.number)


# --------------------------------------------------------------------------
# the pipeline for one issue
# --------------------------------------------------------------------------


def label(number: int, add: list[str] = [], remove: list[str] = []) -> None:
    args = ["issue", "edit", str(number)]
    for name in add:
        args += ["--add-label", name]
    for name in remove:
        args += ["--remove-label", name]
    run(["gh", *args], check=False)


def make_worktree(issue: Issue, branch: str) -> Path:
    path = WORKTREE_PARENT / f"dnd-helper-wt-{issue.number}"
    if path.exists():
        run(["git", "worktree", "remove", str(path), "--force"], REPO_ROOT, check=False)
    run(["git", "worktree", "prune"], REPO_ROOT, check=False)
    run(["git", "worktree", "add", str(path), "-b", branch, "-q"], REPO_ROOT)

    # uv is an order of magnitude faster than venv+pip and is what makes
    # dispatching several issues at once tolerable.
    if shutil.which("uv"):
        run(["uv", "venv", ".venv", "-q"], path, timeout=300)
        run(["uv", "pip", "install", "-q", "-e", ".[dev]"], path, timeout=900)
    else:
        run(["python3", "-m", "venv", ".venv"], path, timeout=300)
        run([".venv/bin/pip", "install", "-q", "-e", ".[dev]"], path, timeout=900)
    return path


def build_prompt(issue: Issue) -> str:
    out = run(["python3", str(HANDOFF), str(issue.number)], cwd=REPO_ROOT)
    return out.stdout + ENV_PREAMBLE


def run_agent(worktree: Path, prompt: str, timeout: int) -> str:
    result = subprocess.run(
        ["codex", "exec", "-s", "workspace-write", "-C", str(worktree), prompt],
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return (result.stdout or "") + (result.stderr or "")


def run_gate(worktree: Path) -> tuple[bool, str]:
    """The definition of done, run where the network and git actually work."""
    steps = [
        ("ruff check", [".venv/bin/ruff", "check", "."]),
        ("ruff format", [".venv/bin/ruff", "format", "--check", "."]),
        ("mypy", [".venv/bin/mypy", "bot/engine", "bot/db"]),
        ("pytest", [".venv/bin/pytest", "-q"]),
    ]
    report = []
    ok = True
    for name, args in steps:
        result = run(args, worktree, check=False, timeout=900)
        passed = result.returncode == 0
        ok = ok and passed
        tail = (result.stdout or result.stderr).strip().splitlines()
        report.append(f"{'PASS' if passed else 'FAIL'}  {name}")
        if not passed:
            report.extend(f"        {line}" for line in tail[-6:])
    return ok, "\n".join(report)


def commit_and_push(worktree: Path, issue: Issue, branch: str) -> bool:
    run(["git", "add", "-A"], worktree)
    status = run(["git", "status", "--porcelain"], worktree)
    if not status.stdout.strip():
        return False
    message = (
        f"{issue.title}\n\nDispatched to a coding agent from issue "
        f"#{issue.number}; gate run and reviewed by the dispatcher.\n\n"
        f"Closes #{issue.number}"
    )
    run(
        [
            "git",
            "-c",
            "user.name=Nick Daniel",
            "-c",
            "user.email=nicholasjdaniel@gmail.com",
            "commit",
            "-q",
            "-m",
            message,
        ],
        worktree,
    )
    run(["git", "push", "-q", "-u", "origin", branch], worktree, timeout=300)
    return True


def open_draft_pr(worktree: Path, issue: Issue, agent_log: str) -> int:
    body = (
        f"Closes #{issue.number}\n\n"
        "**Opened as a draft by `scripts/dispatch.py`.** It becomes ready for "
        "review only once the gate passes outside the agent's sandbox.\n\n"
        "## Agent report\n\n"
        f"```\n{agent_log.strip()[-2500:]}\n```\n\n"
        "## Definition of done\n\n"
        "- [ ] gate (filled in by the dispatcher below)\n"
        "- [ ] manual verification — see the issue\n"
    )
    run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--title",
            issue.title,
            "--body",
            body,
        ],
        worktree,
        timeout=300,
    )
    out = run(["gh", "pr", "view", "--json", "number"], worktree)
    return int(json.loads(out.stdout)["number"])


def attach_review(worktree: Path, pr: int) -> None:
    """An independent pass — the author's own review is a weak signal."""
    result = subprocess.run(
        ["codex", "review", "--base", "main"],
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    findings = (result.stdout or result.stderr).strip()
    marker = "Review comment:"
    if marker in findings:
        findings = findings.split(marker, 1)[1]
    body = (
        "## Independent review (`codex review --base main`)\n\n"
        "Run separately from the agent that wrote this, because whoever wrote "
        "the spec shares its blind spots.\n\n"
        f"```\n{findings[-4000:].strip() or 'No findings.'}\n```"
    )
    run(["gh", "pr", "comment", str(pr), "--body", body], REPO_ROOT, check=False)


def dispatch(issue: Issue, agent_timeout: int) -> str:
    branch = branch_for(issue)
    label(issue.number, add=[IN_PROGRESS], remove=[TODO])
    try:
        worktree = make_worktree(issue, branch)
        agent_log = run_agent(worktree, build_prompt(issue), agent_timeout)

        if not commit_and_push(worktree, issue, branch):
            label(issue.number, add=[TODO, NEEDS_ATTENTION], remove=[IN_PROGRESS])
            return f"#{issue.number}: agent produced no changes"

        pr = open_draft_pr(worktree, issue, agent_log)
        passed, report = run_gate(worktree)
        run(
            [
                "gh",
                "pr",
                "comment",
                str(pr),
                "--body",
                f"## Gate (run outside the agent sandbox)\n\n```\n{report}\n```",
            ],
            REPO_ROOT,
            check=False,
        )

        if not passed:
            label(issue.number, add=[NEEDS_ATTENTION], remove=[IN_PROGRESS])
            return f"#{issue.number}: PR #{pr} left as DRAFT — gate failed"

        run(["gh", "pr", "ready", str(pr)], REPO_ROOT, check=False)
        attach_review(worktree, pr)
        label(issue.number, remove=[IN_PROGRESS])
        return f"#{issue.number}: PR #{pr} ready for review"
    except Exception as exc:  # noqa: BLE001 - one issue must not stop the rest
        label(issue.number, add=[TODO, NEEDS_ATTENTION], remove=[IN_PROGRESS])
        return f"#{issue.number}: FAILED — {exc}"


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=2, help="concurrent agents")
    parser.add_argument("--issue", type=int, help="dispatch one specific issue")
    parser.add_argument("--list", action="store_true", help="show what is ready")
    parser.add_argument("--dry-run", action="store_true", help="plan only")
    parser.add_argument(
        "--agent-timeout", type=int, default=2700, help="seconds per agent"
    )
    args = parser.parse_args(argv)

    try:
        if args.issue:
            raw = gh_json(
                ["issue", "view", str(args.issue), "--json", "number,title,labels"]
            )
            issues = [
                Issue(
                    raw["number"],  # type: ignore[index]
                    raw["title"],  # type: ignore[index]
                    {label["name"] for label in raw["labels"]},  # type: ignore[index]
                )
            ]
        else:
            issues = ready_issues()
    except DispatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not issues:
        print(f"Nothing ready. Label an issue `{TODO}` to queue it.")
        return 0

    if args.list or args.dry_run:
        verb = "Would dispatch" if args.dry_run else "Ready"
        print(f"{verb} ({len(issues)}, cap {args.max}):")
        for issue in issues[: args.max if args.dry_run else len(issues)]:
            print(f"  #{issue.number}  {issue.title}")
        return 0

    batch = issues[: args.max]
    print(f"Dispatching {len(batch)} issue(s):")
    for issue in batch:
        print(f"  #{issue.number}  {issue.title}")

    with ThreadPoolExecutor(max_workers=args.max) as pool:
        results = pool.map(lambda i: dispatch(i, args.agent_timeout), batch)
        print("\nResults:")
        for line in results:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
