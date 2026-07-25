#!/usr/bin/env python3
"""Generate a ready-to-paste handoff prompt for a GitHub issue.

Usage:
    python3 scripts/handoff.py 6          # prompt for issue #6
    python3 scripts/handoff.py --next     # find the next ready issue, then
                                           # prompt for it
    python3 scripts/handoff.py --list     # list all currently-ready issues,
                                           # one per line

See scripts/README.md and docs/WORKFLOW.md for how this fits the loop.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
ARCHITECTURE_DOC = "docs/ARCHITECTURE.md"
CLAUDE_MD_DOC = "CLAUDE.md"

# Labels that make an issue not ready to hand off.
NOT_READY_LABELS = {"blocked", "on-hold", "epic", "decision"}

DEFINITION_OF_DONE_HEADING = "## Definition of done for any task"


class HandoffError(Exception):
    """A user-facing error. Caught at the top level and printed to stderr."""


# --------------------------------------------------------------------------
# gh CLI wrapper
# --------------------------------------------------------------------------


def run_gh(args: list[str]) -> str:
    """Run a `gh` subcommand and return stdout, raising HandoffError on failure."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HandoffError(
            "gh (GitHub CLI) not found on PATH. Install it and run `gh auth login`."
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        lowered = stderr.lower()
        if "auth" in lowered or "not logged" in lowered:
            raise HandoffError(
                "gh is not authenticated. Run `gh auth login` and try again."
            )
        raise HandoffError(f"gh command failed: {stderr or 'unknown error'}")
    return result.stdout


def fetch_issue(number: int) -> dict:
    """Fetch a single issue's number, title, body, labels, and state."""
    try:
        out = run_gh(
            [
                "issue",
                "view",
                str(number),
                "--json",
                "number,title,body,labels,state",
            ]
        )
    except HandoffError as exc:
        if "could not resolve" in str(exc).lower():
            raise HandoffError(f"Issue #{number} not found.") from exc
        raise
    return json.loads(out)


def fetch_open_issues() -> list[dict]:
    """Fetch all open issues' number, title, labels, and state."""
    out = run_gh(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,labels,state",
            "--limit",
            "500",
        ]
    )
    return json.loads(out)


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def label_names(issue: dict) -> set[str]:
    return {label["name"] for label in issue.get("labels", [])}


def is_ready(issue: dict) -> bool:
    """Open and carrying none of the not-ready labels."""
    if issue.get("state") != "OPEN":
        return False
    return label_names(issue).isdisjoint(NOT_READY_LABELS)


CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)


def issues_with_open_prs() -> set[int]:
    """Issue numbers already claimed by an open pull request.

    An issue stays labelled ready until its PR merges, because the label
    tracks dependencies rather than progress. Without this, `--next` keeps
    handing out work that is already written — which in an unattended loop
    means two agents building the same thing.
    """
    out = run_gh(["pr", "list", "--state", "open", "--json", "body", "--limit", "200"])
    claimed: set[int] = set()
    for pr in json.loads(out):
        claimed.update(int(n) for n in CLOSES_RE.findall(pr.get("body") or ""))
    return claimed


def existing_branches() -> set[str]:
    """Every branch name known locally or on the remote.

    A branch matching an issue's derived name means someone — or some agent —
    is already working it, even though no pull request exists yet. Without
    this, an unattended loop hands out in-flight work.
    """
    try:
        out = subprocess.run(
            ["git", "branch", "--all", "--format=%(refname:short)"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    if out.returncode != 0:
        return set()
    names = set()
    for line in out.stdout.split("\n"):
        name = line.strip().removeprefix("origin/")
        if name:
            names.add(name)
    return names


def ready_issues(include_claimed: bool = False) -> list[dict]:
    issues = fetch_open_issues()
    ready = [issue for issue in issues if is_ready(issue)]
    if not include_claimed:
        claimed = issues_with_open_prs()
        branches = existing_branches()
        ready = [
            issue
            for issue in ready
            if issue["number"] not in claimed
            and derive_branch_name(issue["number"], issue["title"]) not in branches
        ]
    ready.sort(key=lambda issue: issue["number"])
    return ready


# --------------------------------------------------------------------------
# Issue body parsing
# --------------------------------------------------------------------------

SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def parse_sections(body: str) -> dict[str, str]:
    """Split a markdown issue body into {heading (lowercase): content}."""
    sections: dict[str, str] = {}
    matches = list(SECTION_HEADING_RE.finditer(body or ""))
    for i, match in enumerate(matches):
        heading = match.group(1).strip().lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[heading] = body[start:end].strip()
    return sections


def get_section(sections: dict[str, str], heading: str) -> str | None:
    content = sections.get(heading.lower())
    if content is None or not content.strip():
        return None
    return content.strip()


def is_automatable(manual_verification: str | None) -> bool:
    """True if the Manual verification section says there's nothing to do."""
    if manual_verification is None:
        return True
    return manual_verification.strip().lower().startswith("none")


def parse_blockers(blocked_by_section: str | None) -> list[str]:
    """Pull issue references like '#14' out of a 'Blocked by' section."""
    if blocked_by_section is None:
        return []
    return re.findall(r"#\d+", blocked_by_section)


# --------------------------------------------------------------------------
# Branch name derivation
# --------------------------------------------------------------------------

TITLE_PREFIX_RE = re.compile(r"^\[([0-9]+(?:\.[0-9A-Za-z]+)?)\]\s*(.*)$")
BRANCH_MAX_LEN = 50


def slugify_words(text: str, max_words: int = 3) -> str:
    """Lowercase, hyphenated slug from the first ~max_words tokens of text."""
    tokens = text.split()[:max_words]
    parts = []
    for token in tokens:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "", token).lower()
        if cleaned:
            parts.append(cleaned)
    return "-".join(parts) if parts else "issue"


def derive_branch_name(number: int, title: str) -> str:
    """Turn an issue title into a branch name.

    `[1.1b] Ollama provider + structured-output conformance test` becomes
    `phase-1/1.1b-ollama-provider`. Titles without a `[x.y]` prefix fall back
    to `task/<number>-<slug>`.
    """
    match = TITLE_PREFIX_RE.match(title)
    if match:
        task_id, rest = match.group(1), match.group(2)
        major = task_id.split(".")[0]
        slug = slugify_words(rest)
        branch = f"phase-{major}/{task_id}-{slug}"
    else:
        slug = slugify_words(title)
        branch = f"task/{number}-{slug}"

    if len(branch) > BRANCH_MAX_LEN:
        branch = branch[:BRANCH_MAX_LEN].rstrip("-")
    return branch


# --------------------------------------------------------------------------
# CLAUDE.md
# --------------------------------------------------------------------------


def read_definition_of_done() -> str:
    """Pull the 'Definition of done for any task' section out of CLAUDE.md."""
    try:
        text = CLAUDE_MD_PATH.read_text()
    except OSError as exc:
        raise HandoffError(f"Could not read {CLAUDE_MD_PATH}: {exc}") from exc

    sections = parse_sections(text)
    content = get_section(sections, "Definition of done for any task")
    if content is None:
        raise HandoffError(
            f"Could not find '{DEFINITION_OF_DONE_HEADING}' in "
            f"{CLAUDE_MD_DOC}. Has it moved or been renamed?"
        )
    return content


# --------------------------------------------------------------------------
# Prompt building
# --------------------------------------------------------------------------


def build_prompt(issue: dict, branch: str, definition_of_done: str) -> str:
    number = issue["number"]
    title = issue["title"]
    sections = parse_sections(issue.get("body") or "")

    scope = get_section(sections, "Scope")
    acceptance = get_section(sections, "Acceptance criteria")
    gotchas = get_section(sections, "Gotchas")
    manual_verification = get_section(sections, "Manual verification")

    lines: list[str] = []
    lines.append(f"Read {CLAUDE_MD_DOC} and {ARCHITECTURE_DOC} before starting.")
    lines.append("")
    lines.append(f"Implement issue #{number}: {title}")
    lines.append("")
    lines.append(f"Branch: {branch}")

    if scope is not None:
        lines.append("")
        lines.append("## Scope")
        lines.append(scope)
    if acceptance is not None:
        lines.append("")
        lines.append("## Acceptance criteria")
        lines.append(acceptance)
    if gotchas is not None:
        lines.append("")
        lines.append("## Gotchas")
        lines.append(gotchas)

    lines.append("")
    lines.append("## Definition of done")
    lines.append(definition_of_done)

    lines.append("")
    lines.append("## Scope guard")
    lines.append("- Do not modify files outside the scope stated above.")
    lines.append("- Do not add dependencies without asking.")
    lines.append("- Do not build ahead into other issues.")
    lines.append(
        "- Do not weaken a failing test to make it pass — if it fails, the "
        "code is wrong, not the test."
    )

    if not is_automatable(manual_verification):
        lines.append("")
        lines.append(
            "A human must perform this issue's Manual verification steps in "
            "a real Discord server. Do not claim the issue is complete "
            "without that being done and reported."
        )

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_list() -> int:
    for issue in ready_issues():
        print(f"#{issue['number']} {issue['title']}")
    return 0


def cmd_next() -> int:
    ready = ready_issues()
    if not ready:
        print("No ready issues found.", file=sys.stderr)
        return 1
    return cmd_issue(ready[0]["number"])


def cmd_issue(number: int) -> int:
    issue = fetch_issue(number)
    names = label_names(issue)
    title = issue["title"]

    if "epic" in names:
        raise HandoffError(
            f"Issue #{number} is an epic ({title!r}). Epics are phase "
            "containers, not implementation work — hand off one of their "
            "sub-issues instead."
        )
    if "decision" in names:
        raise HandoffError(
            f"Issue #{number} is a decision ({title!r}), not implementation "
            "work. Resolve the decision and write the ADR first."
        )
    if "on-hold" in names:
        raise HandoffError(
            f"Issue #{number} is on-hold ({title!r}). Phase 4 needs an "
            "explicit green light before work starts — see docs/ROADMAP.md."
        )

    if issue.get("state") != "OPEN":
        raise HandoffError(
            f"Issue #{number} is not open (state: {issue.get('state')})."
        )

    branch = derive_branch_name(number, title)
    definition_of_done = read_definition_of_done()
    prompt = build_prompt(issue, branch, definition_of_done)

    if "blocked" in names:
        sections = parse_sections(issue.get("body") or "")
        blockers = parse_blockers(get_section(sections, "Blocked by"))
        blocker_text = ", ".join(blockers) if blockers else "(unspecified)"
        print(
            f"WARNING: issue #{number} is labelled 'blocked' by "
            f"{blocker_text}. Printing the prompt anyway, but confirm those "
            "blockers are actually closed before handing this off.",
            file=sys.stderr,
        )

    if number in issues_with_open_prs():
        print(
            f"WARNING: issue #{number} already has an open pull request. "
            "Handing it off again means two agents building the same thing — "
            "review or merge the existing PR instead.",
            file=sys.stderr,
        )

    print(prompt, end="")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a ready-to-paste handoff prompt for a GitHub issue."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("issue", nargs="?", type=int, help="issue number to hand off")
    group.add_argument(
        "--next", action="store_true", help="hand off the next ready issue"
    )
    group.add_argument(
        "--list", action="store_true", help="list all currently-ready issues"
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.list:
            return cmd_list()
        if args.next:
            return cmd_next()
        return cmd_issue(args.issue)
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
