"""Postmortem generation: deterministic facts, LLM prose.

The metadata table, impact numbers, and timeline are code-filled — the
timeline maps 1:1 to recorded events, nothing is invented. One LLM call
fills the narrative sections; if it fails twice, the diagnosis fields are
copied verbatim so the postmortem always publishes.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import Diagnosis, Impact
from ..prompts import POSTMORTEM_PROMPT

_TEMPLATE_PATH = Path(__file__).parent / "template.md"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts


def _timeline_line(event: dict) -> str | None:
    payload = event.get("payload", {})
    kind = event.get("type")
    if kind == "alert_received":
        return f"alert **{payload.get('alertname')}** fired for `{payload.get('endpoint') or payload.get('service')}`"
    if kind == "state_changed":
        return f"incident state: {payload.get('from')} → {payload.get('to')}"
    if kind == "tool_call":
        args = json.dumps(payload.get("args", {}))
        if len(args) > 80:
            args = args[:80] + "…"
        return f"agent ran `{payload.get('tool')}` {args}"
    if kind == "diagnosis_ready":
        suspect = (payload.get("diagnosis") or {}).get("suspect_commit") or {}
        return f"diagnosis ready — suspect commit `{suspect.get('sha', 'unknown')}`"
    if kind == "slack_brief_sent":
        return f"incident brief delivered to {payload.get('delivered_to')}"
    if kind == "alert_resolved":
        return "alert resolved"
    if kind == "postmortem_published":
        return "postmortem published"
    if kind == "agent_error":
        return f"agent error in {payload.get('stage')} (recovered: {payload.get('recovered')})"
    return None  # llm_turn / tool_result / impact_computed are too noisy for the timeline


def build_timeline(events: list[dict]) -> str:
    lines = []
    for event in events:
        line = _timeline_line(event)
        if line:
            lines.append(f"- `{_fmt_ts(event['ts'])}` — {line}")
    return "\n".join(lines) or "- (no events recorded)"


class PostmortemGenerator:
    def __init__(self, llm, model: str, postmortem_dir: str):
        self._llm = llm
        self._model = model
        self._dir = Path(postmortem_dir)

    def generate(self, incident: dict, events: list[dict]) -> tuple[str, str]:
        """Render the postmortem; returns (markdown, file_path). Sync — call via to_thread."""
        diagnosis = Diagnosis(**(incident.get("diagnosis") or {"summary": "", "root_cause": ""}))
        impact = Impact(**(incident.get("impact") or {}))

        # The impact snapshot was frozen at diagnosis time; the incident kept
        # running until resolved_at. Rescale duration-derived numbers to the
        # full incident window so the postmortem doesn't contradict its own
        # Started/Resolved rows. (Rates can't be re-queried here — the fault
        # is already reverted, so Prometheus would report the recovery.)
        duration_min = impact.duration_min
        est_failed_requests = impact.est_failed_requests
        try:
            if incident.get("resolved_at"):
                started = datetime.fromisoformat(incident["created_at"])
                resolved = datetime.fromisoformat(incident["resolved_at"])
                actual_min = max((resolved - started).total_seconds() / 60, 0.0)
                if impact.duration_min > 0:
                    est_failed_requests = int(
                        est_failed_requests * actual_min / impact.duration_min
                    )
                duration_min = actual_min
        except (ValueError, TypeError):
            pass
        impact = impact.model_copy(update={
            "duration_min": round(duration_min, 1),
            "est_failed_requests": est_failed_requests,
        })

        narrative = self._narrative(incident, diagnosis, impact, events)

        suspect = diagnosis.suspect_commit
        values = {
            "title": incident.get("title", "Incident"),
            "incident_id": incident.get("id", "?"),
            "service": incident.get("service", "?"),
            "severity": incident.get("severity", "?"),
            "severity_band": impact.severity_band,
            "started_at": incident.get("created_at", "?"),
            "resolved_at": incident.get("resolved_at") or "?",
            "duration_min": f"{impact.duration_min:.1f}",
            "suspect_commit": (
                f"`{suspect.sha}` {suspect.message} ({suspect.author})" if suspect else "unknown"
            ),
            "runbook_slug": diagnosis.runbook_slug,
            "confidence": diagnosis.confidence,
            "error_rate_pct": f"{impact.error_rate_pct:.1f}",
            "baseline_error_rate_pct": f"{impact.baseline_error_rate_pct:.1f}",
            "est_failed_requests": str(impact.est_failed_requests),
            "p95_ms": f"{impact.p95_ms:.0f}ms" if impact.p95_ms is not None else "n/a",
            "baseline_p95_ms": (
                f"{impact.baseline_p95_ms:.0f}ms" if impact.baseline_p95_ms is not None else "n/a"
            ),
            "requests_per_min": f"{impact.requests_per_min:.0f}",
            "timeline": build_timeline(events),
            "summary": narrative["summary"],
            "root_cause_analysis": narrative["root_cause_analysis"],
            "lessons_learned": "\n".join(f"- {item}" for item in narrative["lessons_learned"]),
            "action_items": "\n".join(f"- [ ] {item}" for item in narrative["action_items"]),
        }

        markdown = _TEMPLATE_PATH.read_text()
        for key, value in values.items():
            markdown = markdown.replace("{{" + key + "}}", value)

        path = self._write(incident, markdown)
        return markdown, path

    def _write(self, incident: dict, markdown: str) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        date = str(incident.get("created_at", ""))[:10] or "undated"
        slug = re.sub(r"[^a-z0-9]+", "-", str(incident.get("title", "incident")).lower()).strip("-")
        path = self._dir / f"{date}-{slug}-{incident.get('id', '')[-4:]}.md"
        path.write_text(markdown)
        return str(path)

    def _narrative(self, incident: dict, diagnosis: Diagnosis,
                   impact: Impact, events: list[dict]) -> dict:
        fallback = {
            "summary": diagnosis.summary,
            "root_cause_analysis": diagnosis.root_cause,
            "lessons_learned": ["(narrative generation unavailable)"],
            "action_items": [diagnosis.remediation or "Review the incident manually."],
        }
        context = (
            f"Diagnosis JSON:\n{diagnosis.model_dump_json()}\n\n"
            f"Impact JSON:\n{impact.model_dump_json()}\n\n"
            f"Timeline:\n{build_timeline(events)}"
        )
        prompt = POSTMORTEM_PROMPT.format(context=context)
        for _attempt in range(2):
            try:
                response = self._llm.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = _THINK_RE.sub("", response.choices[0].message.content or "").strip()
                match = re.search(r"\{.*\}", content, re.DOTALL)
                parsed = json.loads(match.group(0) if match else content)
                if not all(k in parsed for k in fallback):
                    continue
                if not all(isinstance(parsed[k], str)
                           for k in ("summary", "root_cause_analysis")):
                    continue
                if not isinstance(parsed["lessons_learned"], list):
                    parsed["lessons_learned"] = [str(parsed["lessons_learned"])]
                if not isinstance(parsed["action_items"], list):
                    parsed["action_items"] = [str(parsed["action_items"])]
                return parsed
            except Exception:
                continue
        return fallback
