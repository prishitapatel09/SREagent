"""Loop guardrails, exercised with scripted fake LLMs."""

from types import SimpleNamespace

import pytest

from app.events import EventBus
from app.integrations.gitrepo import GitRepo
from app.integrations.logs import ServiceLogs
from app.integrations.slack import SlackNotifier
from app.investigation.loop import InvestigationFailed, Investigator
from app.investigation.stub import _response
from app.investigation.tools import ToolBelt
from app.models import AlertInfo
from app.postmortem.generator import PostmortemGenerator
from app.runbooks.matcher import RunbookMatcher
from app.state import StateMachine
from app.store import Store

from ..conftest import FakeProm


class ScriptedLLM:
    """Returns queued responses; raises queued exceptions."""

    def __init__(self, items):
        self._items = list(items)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        if not self._items:
            raise AssertionError("scripted LLM ran out of responses")
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


ALERT = AlertInfo(
    fingerprint="abc", alertname="HighErrorRate", service="shopapi",
    severity="critical", endpoint="/checkout", summary="checkout failing",
)


def _parts(settings, llm):
    store = Store(settings.db_path)
    bus = EventBus(store)
    state = StateMachine(store, bus)
    prom = FakeProm()
    git = GitRepo(settings.repo_path)
    logs = ServiceLogs(settings.service_log_path)
    matcher = RunbookMatcher(settings.runbooks_dir)
    postmortems = PostmortemGenerator(llm, "test", settings.postmortem_dir)
    investigator = Investigator(
        settings, store, bus, state, prom, git, logs, matcher,
        SlackNotifier(""), postmortems, llm,
    )
    belt = ToolBelt(ALERT, prom, git, logs, matcher, "shopapi",
                    "2026-07-04T18:30:00+00:00")
    return store, investigator, belt


async def test_invalid_diagnosis_gets_one_repair(settings):
    llm = ScriptedLLM([
        # missing required "summary" -> validation error -> repair round
        _response(tool_call=("submit_diagnosis", {
            "root_cause": "x", "suspect_commit_sha": "unknown", "confidence": "low",
        })),
        _response(tool_call=("submit_diagnosis", {
            "summary": "fixed", "root_cause": "x",
            "suspect_commit_sha": "unknown", "confidence": "low",
        })),
    ])
    _, investigator, belt = _parts(settings, llm)
    diagnosis = await investigator._investigate("inc-t", ALERT, belt)
    assert diagnosis.summary == "fixed"
    assert diagnosis.suspect_commit is None


async def test_second_invalid_diagnosis_fails(settings):
    bad = _response(tool_call=("submit_diagnosis", {"root_cause": "only"}))
    llm = ScriptedLLM([bad, bad])
    _, investigator, belt = _parts(settings, llm)
    with pytest.raises(InvestigationFailed):
        await investigator._investigate("inc-t", ALERT, belt)


async def test_no_tool_calls_exhausts_turn_budget(settings):
    llm = ScriptedLLM([_response(content="just musing")] * 20)
    _, investigator, belt = _parts(settings, llm)
    with pytest.raises(InvestigationFailed):
        await investigator._investigate("inc-t", ALERT, belt)


async def test_llm_crash_falls_back_to_heuristic_diagnosis(settings, demo_repo):
    llm = ScriptedLLM([RuntimeError("connection refused")])
    store, investigator, _ = _parts(settings, llm)
    await store.create_incident({
        "id": "inc-t", "created_at": "2026-07-04T18:30:00+00:00",
        "status": "detected", "fingerprint": "abc", "title": "t",
        "alert_json": ALERT.model_dump_json(),
    })

    await investigator.run("inc-t")

    incident = store.get_incident("inc-t")
    assert incident["status"] == "diagnosed"
    assert incident["diagnosis"]["confidence"] == "low"
    assert incident["diagnosis"]["runbook_slug"] == "payment-gateway-outage"
    types = [e["type"] for e in store.events_for("inc-t")]
    assert "agent_error" in types
    assert "diagnosis_ready" in types
    assert "slack_brief_sent" in types
