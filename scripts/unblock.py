#!/usr/bin/env python3
"""Keep the `blocked` label honest.

Every task issue in this repo carries a `## Blocked by` section in its body
listing dependency issue numbers. The `blocked` label should be present
exactly when at least one of those dependencies is still open. Historically
that label was maintained by hand, which means a closed dependency can leave
its dependents mislabelled — and an agent then skips work that is actually
ready.

This script reconciles the label against the real dependency graph:

- Removes `blocked` (and posts a comment) from issues whose dependencies have
  all closed.
- Adds `blocked` to issues that have an open dependency but are missing the
  label (e.g. a dependency got reopened, or a new issue was mislabelled).

Issues labelled `epic`, `decision`, or `on-hold` are skipped entirely — they
are not part of the task dependency chain that this label governs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

BLOCKED_LABEL = "blocked"
SKIP_LABELS = {"epic", "decision", "on-hold"}

# Matches the "## Blocked by" heading, case-insensitively, and captures
# everything up to (but not including) the next "## " heading or the end of
# the body.
BLOCKED_BY_SECTION = re.compile(
    r"^##\s*Blocked by\s*$(?P<section>.*?)(?=^##\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
ISSUE_REF = re.compile(r"#(\d+)")


@dataclass
class Issue:
    number: int
    title: str
    state: str
    body: str
    labels: set[str]
    depends_on: list[int] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    @property
    def is_blocked_labelled(self) -> bool:
        return BLOCKED_LABEL in self.labels

    @property
    def skip(self) -> bool:
        return bool(self.labels & SKIP_LABELS)


def run_gh(args: list[str]) -> str:
    """Run a `gh` subcommand and return its stdout, raising on failure."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def parse_blocked_by(body: str) -> list[int]:
    """Extract dependency issue numbers from a `## Blocked by` section.

    Returns an empty list if the section is absent or names no issues (e.g.
    "Nothing — ready to start.").
    """
    match = BLOCKED_BY_SECTION.search(body)
    if not match:
        return []
    section = match.group("section")
    numbers = [int(n) for n in ISSUE_REF.findall(section)]
    # Preserve first-seen order, drop duplicates.
    seen: set[int] = set()
    ordered: list[int] = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def fetch_all_issues(limit: int) -> dict[int, Issue]:
    """Fetch every issue (open and closed) with body, labels, and state.

    A single `--state all` call gives a complete dependency graph in one
    shot: targets to reconcile come from the open, non-skipped subset, but
    a dependency can point at an issue in any state.
    """
    raw = run_gh(
        [
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels,state",
        ]
    )
    data = json.loads(raw)
    issues: dict[int, Issue] = {}
    for item in data:
        number = item["number"]
        body = item.get("body") or ""
        labels = {label["name"] for label in item.get("labels", [])}
        issue = Issue(
            number=number,
            title=item.get("title", ""),
            state=item.get("state", ""),
            body=body,
            labels=labels,
        )
        issue.depends_on = parse_blocked_by(body)
        issues[number] = issue
    return issues


def open_dependencies(issue: Issue, issues: dict[int, Issue]) -> list[int]:
    """Return the subset of an issue's dependencies that are still open."""
    open_deps = []
    for dep in issue.depends_on:
        dep_issue = issues.get(dep)
        # An unknown dependency number can't be verified as closed; treat it
        # as still open so the issue stays (or becomes) labelled rather than
        # silently losing a real blocker.
        if dep_issue is None or dep_issue.is_open:
            open_deps.append(dep)
    return open_deps


@dataclass
class Action:
    issue: Issue
    kind: str  # "add" or "remove"
    open_deps: list[int]
    closed_deps: list[int]


def plan_actions(issues: dict[int, Issue]) -> list[Action]:
    actions: list[Action] = []
    for issue in issues.values():
        if not issue.is_open or issue.skip:
            continue
        open_deps = open_dependencies(issue, issues)
        closed_deps = [d for d in issue.depends_on if d not in open_deps]
        if issue.is_blocked_labelled and not open_deps:
            actions.append(
                Action(
                    issue=issue,
                    kind="remove",
                    open_deps=open_deps,
                    closed_deps=closed_deps,
                )
            )
        elif not issue.is_blocked_labelled and open_deps:
            actions.append(
                Action(
                    issue=issue,
                    kind="add",
                    open_deps=open_deps,
                    closed_deps=closed_deps,
                )
            )
    return actions


def format_dep_list(numbers: list[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers)


def apply_action(action: Action) -> None:
    number = action.issue.number
    if action.kind == "remove":
        run_gh(["issue", "edit", str(number), "--remove-label", BLOCKED_LABEL])
        deps = format_dep_list(action.closed_deps)
        if deps:
            comment = f"All dependencies closed ({deps}) — no longer blocked."
        else:
            comment = "No open dependencies — no longer blocked."
        run_gh(["issue", "comment", str(number), "--body", comment])
    elif action.kind == "add":
        run_gh(["issue", "edit", str(number), "--add-label", BLOCKED_LABEL])
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown action kind: {action.kind}")


def describe_action(action: Action) -> str:
    number = action.issue.number
    title = action.issue.title
    if action.kind == "remove":
        deps = format_dep_list(action.closed_deps) or "none"
        return (
            f"#{number} {title!r}: remove '{BLOCKED_LABEL}' "
            f"(dependencies now closed: {deps})"
        )
    deps = format_dep_list(action.open_deps)
    return f"#{number} {title!r}: add '{BLOCKED_LABEL}' (open dependency: {deps})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without changing anything.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Max issues to fetch from `gh issue list` (default: 1000).",
    )
    args = parser.parse_args(argv)

    try:
        issues = fetch_all_issues(args.limit)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    actions = plan_actions(issues)

    if not actions:
        print(
            "No changes needed — the `blocked` label already matches "
            "the dependency graph."
        )
        return 0

    verb = "Would apply" if args.dry_run else "Applying"
    print(f"{verb} {len(actions)} change(s):")
    for action in actions:
        print(f"  - {describe_action(action)}")

    if args.dry_run:
        return 0

    for action in actions:
        try:
            apply_action(action)
        except RuntimeError as exc:
            print(f"error acting on #{action.issue.number}: {exc}", file=sys.stderr)
            return 1

    added = sum(1 for a in actions if a.kind == "add")
    removed = sum(1 for a in actions if a.kind == "remove")
    print(f"Done: added '{BLOCKED_LABEL}' to {added}, removed from {removed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
