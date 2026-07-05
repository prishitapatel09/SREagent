"""The agent's hands: OpenAI tool schemas + the registry that executes them.

Schemas are deliberately flat (string/int args only) — small open models
mangle nested parameter schemas. `submit_diagnosis` is the terminal tool;
the loop intercepts it rather than the ToolBelt executing it.
"""

import json

from ..impact import compute_impact
from ..integrations.gitrepo import GitRepo
from ..integrations.logs import ServiceLogs
from ..integrations.prometheus import Prometheus
from ..models import AlertInfo, Impact
from ..runbooks.matcher import RunbookMatcher

SUBMIT_DIAGNOSIS = "submit_diagnosis"


def _tool(name: str, description: str, params: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS = [
    _tool(
        "query_prometheus",
        "Run an instant PromQL query against the metrics backend. "
        "Returns a list of {labels, value} samples.",
        {"query": {"type": "string", "description": "A PromQL expression"}},
        ["query"],
    ),
    _tool(
        "get_service_logs",
        "Tail the service's structured JSON log. Error entries include exception "
        "type and a stack trace with file/line references.",
        {"lines": {"type": "integer", "description": "How many lines to tail (max 200)"}},
        [],
    ),
    _tool(
        "get_recent_commits",
        "List the most recent commits to the service repository (sha, date, author, subject).",
        {"limit": {"type": "integer", "description": "How many commits (max 15)"}},
        [],
    ),
    _tool(
        "get_commit_diff",
        "Show the full diff and stats of one commit.",
        {"sha": {"type": "string", "description": "Abbreviated commit sha from the commit list"}},
        ["sha"],
    ),
    _tool(
        "search_commits",
        "Find commits whose diff added or removed an exact string (git log -S). "
        "Use a distinctive string from logs or stack traces: a function name, "
        "hostname, or error message fragment.",
        {"text": {"type": "string", "description": "Exact string to search commit diffs for"}},
        ["text"],
    ),
    _tool(
        "search_runbooks",
        "Search the team runbooks. Returns the top matches with scores and matched terms.",
        {"query": {"type": "string",
                   "description": "Keywords: alert name, endpoint, error strings"}},
        ["query"],
    ),
    _tool(
        "get_runbook",
        "Read the full markdown of one runbook by slug.",
        {"slug": {"type": "string", "description": "Runbook slug from search_runbooks"}},
        ["slug"],
    ),
    _tool(
        "calculate_user_impact",
        "Compute user-impact numbers (error rate vs baseline, estimated failed "
        "requests, p95 latency) deterministically from metrics. Takes no arguments.",
        {},
        [],
    ),
    _tool(
        SUBMIT_DIAGNOSIS,
        "Submit your final diagnosis. This ends the investigation.",
        {
            "summary": {"type": "string", "description": "1-2 sentence incident summary"},
            "root_cause": {"type": "string", "description": "The root cause, with evidence"},
            "suspect_commit_sha": {"type": "string",
                                   "description": "Abbreviated sha of the offending commit, or 'unknown'"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "runbook_slug": {"type": "string",
                             "description": "Slug of the most relevant runbook, or 'none'"},
            "remediation": {"type": "string", "description": "Recommended immediate mitigation"},
            "evidence": {"type": "array", "items": {"type": "string"},
                         "description": "Key evidence, one finding per string"},
        },
        ["summary", "root_cause", "suspect_commit_sha", "confidence"],
    ),
]


class ToolBelt:
    """Per-incident tool executor. Everything returns a string for the LLM."""

    def __init__(self, alert: AlertInfo, prom: Prometheus, git: GitRepo,
                 logs: ServiceLogs, matcher: RunbookMatcher,
                 prometheus_job: str, incident_started_at: str):
        self._alert = alert
        self._prom = prom
        self._git = git
        self._logs = logs
        self._matcher = matcher
        self._job = prometheus_job
        self._started_at = incident_started_at
        self.last_impact: Impact | None = None

    def execute(self, name: str, args: dict) -> str:
        handlers = {
            "query_prometheus": lambda: self._query_prometheus(str(args.get("query", ""))),
            "get_service_logs": lambda: self._logs.tail(int(args.get("lines", 100) or 100)),
            "get_recent_commits": lambda: self._git.recent_commits(int(args.get("limit", 10) or 10)),
            "get_commit_diff": lambda: self._git.commit_diff(str(args.get("sha", ""))),
            "search_commits": lambda: self._git.search_commits(str(args.get("text", ""))),
            "search_runbooks": lambda: self._search_runbooks(str(args.get("query", ""))),
            "get_runbook": lambda: self._get_runbook(str(args.get("slug", ""))),
            "calculate_user_impact": self._calculate_user_impact,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"(unknown tool: {name} — available: {', '.join(handlers)})"
        try:
            return handler()
        except Exception as exc:  # tool errors inform the LLM, never kill the loop
            return f"(tool error in {name}: {type(exc).__name__}: {exc})"

    def _query_prometheus(self, query: str) -> str:
        results = self._prom.query(query)[:20]
        if not results:
            return "(query returned no data — check label names and time window)"
        return json.dumps(
            [{"labels": r["labels"], "value": round(r["value"], 4)} for r in results]
        )

    def _search_runbooks(self, query: str) -> str:
        matches = self._matcher.search(query or self._alert.alertname)
        if not matches:
            return "(no runbooks found)"
        return json.dumps(matches)

    def _get_runbook(self, slug: str) -> str:
        body = self._matcher.get(slug)
        if body is None:
            return f"(no runbook with slug {slug!r}; known slugs: {', '.join(self._matcher.slugs())})"
        return body

    def _calculate_user_impact(self) -> str:
        impact = compute_impact(
            self._prom, self._job, self._alert.endpoint, self._started_at
        )
        self.last_impact = impact
        return impact.model_dump_json()
