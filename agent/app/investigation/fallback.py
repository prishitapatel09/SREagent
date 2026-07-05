"""Deterministic fallback diagnosis for when the LLM fails entirely.

The demo (and the pipeline) can never dead-end: this synthesizes a low-
confidence diagnosis from already-available artifacts — alert annotations,
the most recent commit, and the top BM25 runbook match.
"""

from ..integrations.gitrepo import GitRepo
from ..models import AlertInfo, Diagnosis, SuspectCommit
from ..runbooks.matcher import RunbookMatcher


def build_fallback_diagnosis(alert: AlertInfo, git: GitRepo,
                             matcher: RunbookMatcher) -> Diagnosis:
    matches = matcher.search(f"{alert.alertname} {alert.endpoint} {alert.summary}")
    runbook_slug = matches[0]["slug"] if matches else "none"

    suspect = None
    head_line = git.recent_commits(1).splitlines()[0] if git else ""
    if head_line and not head_line.startswith("("):
        meta = git.commit_meta(head_line.split()[0])
        if meta:
            suspect = SuspectCommit(**meta)

    return Diagnosis(
        summary=alert.summary or f"{alert.alertname} firing on {alert.endpoint or alert.service}",
        root_cause=(
            "Not determined automatically (LLM unavailable). The most recent "
            "change is the default suspect — verify manually."
        ),
        suspect_commit=suspect,
        confidence="low",
        runbook_slug=runbook_slug,
        remediation="Follow the matched runbook; consider rolling back the most recent change.",
        evidence=["heuristic fallback: LLM unavailable or investigation failed"],
    )
