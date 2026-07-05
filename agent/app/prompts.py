"""Every prompt in the system, in one skimmable file.

The numbered playbook is the key small-model trick: Qwen follows an explicit
procedure far more reliably than an open-ended "investigate this".
"""

from .models import AlertInfo

SYSTEM_PROMPT = """\
You are an SRE agent investigating a production alert for the service `shopapi`.

Follow this procedure:
1. Confirm the symptom with `query_prometheus`.
2. Check `get_service_logs` for errors. Note exception types, distinctive strings
   (function names, hostnames, error messages), and file/line references in stack traces.
3. Correlate with recent changes: `search_commits` finds the commit whose diff introduced
   a distinctive string from the logs; `get_recent_commits` lists recent commits;
   `get_commit_diff` shows what a specific commit changed.
4. Find remediation guidance with `search_runbooks`, then read the best match with `get_runbook`.
5. Call `calculate_user_impact` to get computed impact numbers.
When you are confident — or when told your budget is exhausted — call `submit_diagnosis`.

Rules:
- Call exactly one tool at a time.
- Cite evidence only from tool results. Never invent commit shas, numbers, or file names.
- A recent code or config change is the most likely root cause. Feature-flag changes
  appear in the service logs as `flag_change` events.
- Metrics available: `http_requests_total{job="shopapi", endpoint, method, status}` (counter)
  and `http_request_duration_seconds_bucket{job="shopapi", endpoint, le}` (histogram).
  Example queries:
    sum by (endpoint) (rate(http_requests_total{job="shopapi", status=~"5.."}[1m]))
    histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job="shopapi", endpoint="/products"}[1m])))
"""


def system_prompt(model: str) -> str:
    prompt = SYSTEM_PROMPT
    if "qwen3" in model.lower():
        # Soft switch: disables qwen3's thinking mode, which would otherwise
        # multiply per-turn latency in a live demo.
        prompt += "\n/no_think"
    return prompt


def alert_message(alert: AlertInfo) -> str:
    return (
        "A production alert is firing. Investigate it.\n\n"
        f"alertname: {alert.alertname}\n"
        f"service: {alert.service}\n"
        f"severity: {alert.severity}\n"
        f"endpoint: {alert.endpoint or '(not labeled)'}\n"
        f"summary: {alert.summary}\n"
        f"description: {alert.description}\n"
        f"firing since: {alert.starts_at}"
    )


NUDGE_MESSAGE = (
    "Continue the investigation by calling exactly one tool, "
    "or call submit_diagnosis if you have enough evidence."
)

POSTMORTEM_PROMPT = """\
You are writing the narrative sections of an incident postmortem.
Given the diagnosis, impact numbers, and event timeline below, respond with ONLY a JSON
object with these keys:
  "summary": 2-3 sentence executive summary of what happened,
  "root_cause_analysis": one paragraph explaining the root cause and how it manifested,
  "lessons_learned": array of 2-4 short strings,
  "action_items": array of 2-4 short imperative strings.
Quote numbers exactly as given — do not recompute or round them. Do not invent facts.

{context}
"""
