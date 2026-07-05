"""StubLLM: a deterministic, scripted investigator with the openai duck-type.

Used by tests (no network, no model) and as the live-demo fallback if Ollama
is down. It is a state machine over the conversation, not a canned transcript:
it decides its next move from which tool results are already in `messages`,
calls the REAL tools, and extracts the suspect commit sha from the REAL
`search_commits` output — so its diagnosis is truthful, just not clever.
"""

import json
import re
from types import SimpleNamespace

# alert endpoint -> the distinctive string the investigation pivots on.
# These mirror what a competent human would grep for after reading the logs.
SEARCH_HINTS = {
    "/checkout": "payments-v2.internal",
    "/products": "get_stock",
    "/orders/{order_id}": "parse_order_timestamps",
}


def _response(content: str | None = None, tool_call: tuple[str, dict] | None = None):
    tool_calls = None
    if tool_call is not None:
        name, args = tool_call
        tool_calls = [SimpleNamespace(
            id=f"call_{name}",
            type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )]
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class StubLLM:
    def __init__(self):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, model=None, messages=None, tools=None, tool_choice=None, **_kw):
        messages = messages or []
        if not tools:
            return self._narrative(messages)
        return self._investigate(messages, tool_choice)

    # -- investigation script ------------------------------------------------

    def _investigate(self, messages: list[dict], tool_choice):
        alert_text = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"), ""
        )
        endpoint = self._field(alert_text, "endpoint") or ""
        alertname = self._field(alert_text, "alertname") or "alert"
        hint = SEARCH_HINTS.get(endpoint, alertname)
        tool_results = [m for m in messages if m.get("role") == "tool"]
        step = len(tool_results)

        forced = (
            isinstance(tool_choice, dict)
            and tool_choice.get("function", {}).get("name") == "submit_diagnosis"
        )
        if forced or step >= 6:
            return self._submit(messages, endpoint, alertname)

        script = [
            ("query_prometheus",
             {"query": 'sum by (endpoint) (rate(http_requests_total{job="shopapi", status=~"5.."}[1m]))'}),
            ("get_service_logs", {"lines": 80}),
            ("search_commits", {"text": hint}),
            ("get_commit_diff", {"sha": self._sha_from(tool_results)}),
            ("search_runbooks", {"query": f"{alertname} {endpoint} {hint}"}),
            ("calculate_user_impact", {}),
        ]
        name, args = script[step]
        return _response(content="", tool_call=(name, args))

    def _submit(self, messages: list[dict], endpoint: str, alertname: str):
        tool_results = [m for m in messages if m.get("role") == "tool"]
        sha = self._sha_from(tool_results)
        slug = self._slug_from(tool_results)
        confidence = "high" if sha != "unknown" else "low"
        args = {
            "summary": f"{alertname} on {endpoint or 'shopapi'}: traced to a recent code change.",
            "root_cause": (
                f"A recent commit ({sha}) introduced the failing code path on "
                f"{endpoint or 'the service'}; enabling its feature flag activated the bug."
            ),
            "suspect_commit_sha": sha,
            "confidence": confidence,
            "runbook_slug": slug,
            "remediation": "Disable the offending feature flag to roll back the change (see runbook).",
            "evidence": [
                "5xx/latency confirmed via Prometheus",
                "service log stack trace names the failing module",
                f"git log -S traced the distinctive string to commit {sha}",
            ],
        }
        return _response(tool_call=("submit_diagnosis", args))

    # -- parsing helpers (over real tool output) ------------------------------

    @staticmethod
    def _field(alert_text: str, name: str) -> str | None:
        match = re.search(rf"^{name}: (.+)$", alert_text, re.MULTILINE)
        if not match:
            return None
        value = match.group(1).strip()
        return "" if value.startswith("(") else value

    @staticmethod
    def _sha_from(tool_results: list[dict]) -> str:
        # The search_commits result is the 3rd tool result; first token is the sha.
        if len(tool_results) >= 3:
            first_line = str(tool_results[2].get("content", "")).splitlines()[0]
            token = first_line.split()[0] if first_line.split() else ""
            if re.fullmatch(r"[0-9a-f]{4,40}", token):
                return token
        return "unknown"

    @staticmethod
    def _slug_from(tool_results: list[dict]) -> str:
        # The search_runbooks result is the 5th tool result (JSON list).
        if len(tool_results) >= 5:
            try:
                matches = json.loads(str(tool_results[4].get("content", "")))
                if matches:
                    return matches[0]["slug"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        return "none"

    # -- postmortem narrative --------------------------------------------------

    @staticmethod
    def _narrative(messages: list[dict]):
        text = " ".join(str(m.get("content", "")) for m in messages)
        sha_match = re.search(r"\b[0-9a-f]{7,40}\b", text)
        sha = sha_match.group(0) if sha_match else "a recent commit"
        narrative = {
            "summary": (
                "An alert fired after a feature-flag rollout activated a defective "
                f"code path introduced by commit {sha}. The agent identified the "
                "commit, matched a runbook, and the flag was disabled to restore service."
            ),
            "root_cause_analysis": (
                f"The change in commit {sha} shipped dormant behind a feature flag. "
                "Enabling the flag routed production traffic through the new code path, "
                "which failed against real data/dependencies in a way pre-production "
                "checks did not exercise."
            ),
            "lessons_learned": [
                "Flag rollouts are deploys: stage them and watch error budgets during ramp.",
                "Distinctive error strings in logs made the offending commit findable in seconds.",
            ],
            "action_items": [
                "Add a canary stage for feature-flag rollouts.",
                "Add a regression test covering the failing input shape.",
            ],
        }
        return _response(content=json.dumps(narrative))
