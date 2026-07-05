"""The investigation loop: an OpenAI tool-calling loop with guardrails.

Guardrails, in order of engagement (all tuned for small open models):
1. Budget: at most `max_tool_calls` tool executions and MAX_TURNS LLM turns.
2. One tool per turn — enforced in code (Ollama ignores parallel_tool_calls).
3. Forced finish: at budget-1, tool_choice forces `submit_diagnosis`.
4. Validation + one repair round on the diagnosis arguments.
5. Deterministic fallback diagnosis if the LLM fails entirely.

Blocking work (LLM calls, tools) runs via asyncio.to_thread so the event
loop — and therefore the dashboard's SSE stream — never stalls.
"""

import asyncio
import json
import re

from pydantic import ValidationError

from ..config import Settings
from ..events import EventBus
from ..impact import compute_impact
from ..integrations.gitrepo import GitRepo
from ..integrations.logs import ServiceLogs
from ..integrations.prometheus import Prometheus
from ..integrations.slack import SlackNotifier
from ..models import AlertInfo, Diagnosis, DiagnosisArgs, Impact, SuspectCommit
from ..postmortem.generator import PostmortemGenerator
from ..prompts import NUDGE_MESSAGE, alert_message, system_prompt
from ..runbooks.matcher import RunbookMatcher
from ..state import StateMachine
from ..store import Store
from .fallback import build_fallback_diagnosis
from .tools import SUBMIT_DIAGNOSIS, TOOL_SCHEMAS, ToolBelt

MAX_TURNS = 15
RESULT_PREVIEW_CHARS = 1500
TOOL_MESSAGE_CHARS = 6000

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class InvestigationFailed(Exception):
    pass


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


class Investigator:
    def __init__(self, settings: Settings, store: Store, bus: EventBus,
                 state: StateMachine, prom: Prometheus, git: GitRepo,
                 logs: ServiceLogs, matcher: RunbookMatcher,
                 slack: SlackNotifier, postmortems: PostmortemGenerator, llm):
        self._settings = settings
        self._store = store
        self._bus = bus
        self._state = state
        self._prom = prom
        self._git = git
        self._logs = logs
        self._matcher = matcher
        self._slack = slack
        self._postmortems = postmortems
        self._llm = llm

    # -- lifecycle ----------------------------------------------------------

    async def run(self, incident_id: str) -> None:
        """Full investigation for one incident. Runs as a background task."""
        incident = self._store.get_incident(incident_id)
        alert = AlertInfo(**incident["alert"])
        await self._state.advance(incident_id, "investigating")

        belt = ToolBelt(
            alert, self._prom, self._git, self._logs, self._matcher,
            self._settings.prometheus_job, incident["created_at"],
        )

        try:
            diagnosis = await self._investigate(incident_id, alert, belt)
        except Exception as exc:
            await self._bus.emit(incident_id, "agent_error", {
                "stage": "investigation",
                "message": f"{type(exc).__name__}: {exc}",
                "recovered": True,
            })
            diagnosis = await asyncio.to_thread(
                build_fallback_diagnosis, alert, self._git, self._matcher
            )

        impact = await self._compute_impact(incident_id, alert, belt)
        await self._bus.emit(incident_id, "impact_computed", {"impact": impact.model_dump()})

        await self._store.update_incident(
            incident_id,
            diagnosis_json=diagnosis.model_dump_json(),
            impact_json=impact.model_dump_json(),
        )
        await self._state.advance(incident_id, "diagnosed")
        await self._bus.emit(incident_id, "diagnosis_ready", {"diagnosis": diagnosis.model_dump()})

        incident = self._store.get_incident(incident_id)
        delivered_to, text = await asyncio.to_thread(
            self._slack.send_brief, incident, diagnosis, impact
        )
        await self._bus.emit(incident_id, "slack_brief_sent", {
            "delivered_to": delivered_to, "text_fallback": text,
        })

        # If the alert resolved while we were still investigating, finish now.
        await self.maybe_finalize(incident_id)

    async def maybe_finalize(self, incident_id: str) -> None:
        """diagnosed + resolved_at set -> resolved -> postmortem_published.

        Resumable: an incident stranded in "resolved" by a restart (postmortem
        generation interrupted) picks up where it left off.
        """
        incident = self._store.get_incident(incident_id)
        if incident is None or not incident["resolved_at"]:
            return
        if incident["status"] == "diagnosed":
            await self._state.advance(incident_id, "resolved")
        elif incident["status"] != "resolved":
            return
        try:
            incident = self._store.get_incident(incident_id)
            if not incident.get("postmortem_md"):
                events = self._store.events_for(incident_id)
                markdown, path = await asyncio.to_thread(
                    self._postmortems.generate, incident, events
                )
                await self._store.update_incident(
                    incident_id, postmortem_md=markdown, postmortem_path=path
                )
                await self._bus.emit(incident_id, "postmortem_published", {
                    "path": path,
                    "url": f"/api/incidents/{incident_id}/postmortem",
                })
            await self._state.advance(incident_id, "postmortem_published")
        except Exception as exc:
            await self._bus.emit(incident_id, "agent_error", {
                "stage": "postmortem",
                "message": f"{type(exc).__name__}: {exc}",
                "recovered": False,
            })

    # -- the loop -----------------------------------------------------------

    async def _investigate(self, incident_id: str, alert: AlertInfo,
                           belt: ToolBelt) -> Diagnosis:
        messages: list[dict] = [
            {"role": "system", "content": system_prompt(self._settings.llm_model)},
            {"role": "user", "content": alert_message(alert)},
        ]
        tool_calls_used = 0
        repair_used = False
        force_submit = False

        for turn in range(MAX_TURNS):
            force = force_submit or tool_calls_used >= self._settings.max_tool_calls - 1
            response = await asyncio.to_thread(self._chat, messages, force)
            message = response.choices[0].message
            assistant_text = _strip_think(message.content)
            await self._bus.emit(incident_id, "llm_turn", {
                "turn": turn, "assistant_text": assistant_text[:500],
            })

            tool_calls = list(message.tool_calls or [])
            if not tool_calls:
                messages.append({"role": "assistant", "content": assistant_text or "..."})
                messages.append({"role": "user", "content": NUDGE_MESSAGE})
                continue

            call = tool_calls[0]
            name = call.function.name
            raw_args = call.function.arguments or "{}"
            messages.append({
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": [{
                    "id": call.id, "type": "function",
                    "function": {"name": name, "arguments": raw_args},
                }],
            })

            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    raise json.JSONDecodeError("not an object", raw_args, 0)
            except json.JSONDecodeError:
                messages.append(_tool_result(call.id,
                    "(your tool arguments were not valid JSON — try again)"))
                continue

            if name == SUBMIT_DIAGNOSIS:
                try:
                    parsed = DiagnosisArgs(**args)
                    return await self._to_diagnosis(parsed)
                except ValidationError as exc:
                    if repair_used:
                        raise InvestigationFailed(f"diagnosis failed validation twice: {exc}")
                    repair_used = True
                    force_submit = True
                    messages.append(_tool_result(call.id,
                        f"(diagnosis rejected — fix these fields and resubmit: {exc})"))
                    continue

            if force:
                # Some backends (Ollama included) silently ignore tool_choice,
                # so the budget must also be enforced in code: refuse the tool
                # and tell the model to submit.
                messages.append(_tool_result(call.id, (
                    "(tool budget exhausted — do not call more investigation "
                    "tools; call submit_diagnosis now with your findings)"
                )))
                continue

            note = ""
            if len(tool_calls) > 1:
                note = ("\n(note: you sent multiple tool calls; only the first was "
                        "executed — call one tool at a time)")
            await self._bus.emit(incident_id, "tool_call", {
                "turn": turn, "tool": name, "args": args,
            })
            result = await asyncio.to_thread(belt.execute, name, args)
            tool_calls_used += 1
            await self._bus.emit(incident_id, "tool_result", {
                "turn": turn, "tool": name,
                "result_preview": result[:RESULT_PREVIEW_CHARS],
                "truncated": len(result) > RESULT_PREVIEW_CHARS,
            })
            messages.append(_tool_result(call.id, result[:TOOL_MESSAGE_CHARS] + note))

        raise InvestigationFailed("turn budget exhausted without a diagnosis")

    def _chat(self, messages: list[dict], force_submit: bool):
        kwargs: dict = {
            "model": self._settings.llm_model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "temperature": 0,
        }
        if force_submit:
            kwargs["tool_choice"] = {
                "type": "function", "function": {"name": SUBMIT_DIAGNOSIS},
            }
        return self._llm.chat.completions.create(**kwargs)

    async def _to_diagnosis(self, args: DiagnosisArgs) -> Diagnosis:
        suspect = None
        sha = args.suspect_commit_sha.strip()
        if sha and sha.lower() != "unknown":
            meta = await asyncio.to_thread(self._git.commit_meta, sha)
            suspect = SuspectCommit(**meta) if meta else SuspectCommit(sha=sha)
        return Diagnosis(
            summary=args.summary,
            root_cause=args.root_cause,
            suspect_commit=suspect,
            confidence=args.confidence,
            runbook_slug=args.runbook_slug or "none",
            remediation=args.remediation,
            evidence=args.evidence,
        )

    async def _compute_impact(self, incident_id: str, alert: AlertInfo,
                              belt: ToolBelt) -> Impact:
        if belt.last_impact is not None:
            return belt.last_impact
        incident = self._store.get_incident(incident_id)
        try:
            return await asyncio.to_thread(
                compute_impact, self._prom, self._settings.prometheus_job,
                alert.endpoint, incident["created_at"],
            )
        except Exception as exc:
            await self._bus.emit(incident_id, "agent_error", {
                "stage": "impact",
                "message": f"{type(exc).__name__}: {exc}",
                "recovered": True,
            })
            return Impact()


def _tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}
