"""Slack incident brief. Built only from Diagnosis + Impact JSON — no
free-form LLM text goes to Slack, so the brief can't ramble or hallucinate.

If no webhook URL is configured, the text brief is emitted as a console/
dashboard fallback event instead — the demo works without Slack.
"""

import httpx

from ..models import Diagnosis, Impact

_BAND_EMOJI = {"major": ":rotating_light:", "moderate": ":warning:", "minor": ":information_source:"}


def build_brief_text(incident: dict, diagnosis: Diagnosis, impact: Impact) -> str:
    suspect = diagnosis.suspect_commit
    suspect_line = (
        f"{suspect.sha} — {suspect.message} ({suspect.author})" if suspect else "unknown"
    )
    p95 = f"{impact.p95_ms:.0f}ms" if impact.p95_ms is not None else "n/a"
    lines = [
        f"[{impact.severity_band.upper()}] {incident.get('title', 'Incident')}",
        f"Summary: {diagnosis.summary}",
        f"Root cause: {diagnosis.root_cause}",
        f"Suspect commit: {suspect_line}",
        (
            f"Impact: {impact.error_rate_pct:.1f}% of requests failing "
            f"(baseline {impact.baseline_error_rate_pct:.1f}%), "
            f"~{impact.est_failed_requests} failed requests over "
            f"{impact.duration_min:.1f} min, p95 {p95}"
        ),
        f"Confidence: {diagnosis.confidence} | Runbook: {diagnosis.runbook_slug}",
        f"Remediation: {diagnosis.remediation}",
    ]
    return "\n".join(lines)


def _block_kit(incident: dict, diagnosis: Diagnosis, impact: Impact) -> dict:
    emoji = _BAND_EMOJI.get(impact.severity_band, ":warning:")
    suspect = diagnosis.suspect_commit
    suspect_text = (
        f"`{suspect.sha}` {suspect.message}\n_{suspect.author}_" if suspect else "unknown"
    )
    p95 = f"{impact.p95_ms:.0f}ms" if impact.p95_ms is not None else "n/a"
    return {
        "blocks": [
            {"type": "header", "text": {
                "type": "plain_text",
                "text": f"{emoji} [{impact.severity_band.upper()}] {incident.get('title', 'Incident')}",
                "emoji": True,
            }},
            {"type": "section", "text": {"type": "mrkdwn", "text": diagnosis.summary}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Suspect commit:*\n{suspect_text}"},
                {"type": "mrkdwn", "text": f"*Runbook:*\n{diagnosis.runbook_slug}"},
                {"type": "mrkdwn", "text": f"*Error rate:*\n{impact.error_rate_pct:.1f}% (baseline {impact.baseline_error_rate_pct:.1f}%)"},
                {"type": "mrkdwn", "text": f"*p95 / failed reqs:*\n{p95} / ~{impact.est_failed_requests}"},
            ]},
            {"type": "section", "text": {
                "type": "mrkdwn",
                "text": f"*Root cause:* {diagnosis.root_cause}\n*Remediation:* {diagnosis.remediation}",
            }},
            {"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"confidence: {diagnosis.confidence} · incident {incident.get('id', '?')} · sent by SREagent",
            }]},
        ]
    }


class SlackNotifier:
    def __init__(self, webhook_url: str):
        self._url = webhook_url.strip()

    def send_brief(self, incident: dict, diagnosis: Diagnosis,
                   impact: Impact) -> tuple[str, str]:
        """Returns (delivered_to, text_fallback)."""
        text = build_brief_text(incident, diagnosis, impact)
        if not self._url:
            return "console", text
        try:
            response = httpx.post(
                self._url, json=_block_kit(incident, diagnosis, impact), timeout=10.0
            )
            response.raise_for_status()
            return "slack", text
        except httpx.HTTPError:
            return "console", text
