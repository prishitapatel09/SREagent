"""App factory: wires every component together and serves the dashboard.

One FastAPI app, one container: webhook + agent loop + read API + SSE +
static dashboard. The agent emits events in-process (EventBus) — no broker.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import webhook
from .config import Settings
from .dashboard import routes as dashboard_routes
from .events import EventBus
from .integrations.gitrepo import GitRepo
from .integrations.logs import ServiceLogs
from .integrations.prometheus import Prometheus
from .integrations.slack import SlackNotifier
from .investigation.llm import make_llm_client
from .investigation.loop import Investigator
from .postmortem.generator import PostmortemGenerator
from .runbooks.matcher import RunbookMatcher
from .state import StateMachine
from .store import Store

STATIC_DIR = Path(__file__).parent / "dashboard" / "static"


class Runtime:
    """Everything the request handlers and background tasks need."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.bus = EventBus(self.store)
        self.state = StateMachine(self.store, self.bus)
        self.prom = Prometheus(settings.prometheus_url)
        self.git = GitRepo(settings.repo_path)
        self.logs = ServiceLogs(settings.service_log_path)
        self.matcher = RunbookMatcher(settings.runbooks_dir)
        self.llm = make_llm_client(settings)
        self.slack = SlackNotifier(settings.slack_webhook_url)
        self.postmortems = PostmortemGenerator(
            self.llm, settings.llm_model, settings.postmortem_dir
        )
        self.investigator = Investigator(
            settings, self.store, self.bus, self.state, self.prom, self.git,
            self.logs, self.matcher, self.slack, self.postmortems, self.llm,
        )
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


async def _recover_unfinished(runtime: Runtime) -> None:
    """Resume incidents stranded in a non-terminal state by an agent restart.

    Without this, an orphaned "investigating" row would dedup every future
    firing webhook for the same alert while never producing a diagnosis.
    """
    for row in runtime.store.list_unfinished():
        incident_id, status = row["id"], row["status"]
        if status in ("detected", "investigating"):
            # investigator.run() opens with detected -> investigating, so
            # reset first to keep the state machine's transition table honest.
            await runtime.store.update_incident(incident_id, status="detected")
            await runtime.bus.emit(incident_id, "agent_error", {
                "stage": "recovery",
                "message": "agent restarted mid-investigation; investigation restarted",
                "recovered": True,
            })
            runtime.spawn(runtime.investigator.run(incident_id))
        elif status in ("diagnosed", "resolved"):
            # Finishes resolved-but-unpublished incidents; no-ops if the
            # incident is just waiting for its resolved webhook.
            runtime.spawn(runtime.investigator.maybe_finalize(incident_id))


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime(settings or Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _recover_unfinished(runtime)
        yield

    app = FastAPI(title="SREagent", lifespan=lifespan)
    app.state.runtime = runtime
    app.include_router(webhook.router)
    app.include_router(dashboard_routes.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
    return app
