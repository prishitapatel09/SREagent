"""Shared fixtures: tmp SQLite, a small seeded git repo, a fake Prometheus,
and the full app assembled in stub mode with fixture-backed tools."""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.integrations.prometheus import Prometheus
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
RUNBOOKS_DIR = REPO_ROOT / "runbooks"


class FakeProm(Prometheus):
    """Duck-typed Prometheus returning canned samples (inherits .scalar)."""

    def __init__(self, total=0.166, errors=0.05, p95=0.13, baseline_available=False):
        self._total = total
        self._errors = errors
        self._p95 = p95
        self._baseline_available = baseline_available

    def query(self, promql: str) -> list[dict]:
        # Regression guard for the class of bug a canned fake can hide:
        # `sum(rate(...)) offset 5m` is invalid PromQL (offset must sit inside
        # the range selector) and a real Prometheus would return HTTP 400.
        assert not re.search(r"\)\s+offset", promql), f"invalid PromQL: {promql}"
        if "offset" in promql and not self._baseline_available:
            return []
        if "histogram_quantile" in promql:
            value = self._p95
        elif 'status=~"5.."' in promql:
            value = self._errors
        else:
            value = self._total
        return [{"labels": {"endpoint": "/checkout"}, "value": value}]


def _git(repo: Path, *args: str, author_env: dict | None = None) -> str:
    import os

    env = dict(os.environ)
    env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")
    if author_env:
        env.update(author_env)
    result = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def demo_repo(tmp_path):
    """A tiny git repo with one 'bad commit' introducing a distinctive string."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "payment_client.py").write_text(
        "def charge(order):\n    return {'charged': True}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: initial payment client")

    (repo / "payment_client.py").write_text(
        'PAYMENTS_V2_URL = "http://payments-v2.internal:9443/charge"\n\n'
        "def charge(order):\n    return charge_v2(order)\n\n"
        "def charge_v2(order):\n    raise RuntimeError('gateway unreachable')\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(payments): payments v2 client behind payments_v2 flag")
    bad_sha = _git(repo, "rev-parse", "--short", "HEAD")

    (repo / "README.md").write_text("# demo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: add readme")

    return SimpleNamespace(path=repo, bad_sha=bad_sha)


@pytest.fixture
def service_log(tmp_path):
    log_path = tmp_path / "app.log"
    lines = [
        {"ts": "2026-07-04T18:30:20.000+00:00", "level": "ERROR", "endpoint": "/checkout",
         "method": "POST", "status": 502, "message": "payment charge failed",
         "exc_type": "PaymentGatewayError",
         "stack": 'File "payment_client.py", line 4, in charge_v2'},
    ] * 5
    log_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return log_path


@pytest.fixture
def settings(tmp_path, demo_repo, service_log):
    return Settings(
        agent_mode="stub",
        db_path=str(tmp_path / "test.db"),
        repo_path=str(demo_repo.path),
        runbooks_dir=str(RUNBOOKS_DIR),
        service_log_path=str(service_log),
        postmortem_dir=str(tmp_path / "postmortems"),
        slack_webhook_url="",
        prometheus_url="http://prometheus.invalid:9090",
    )


@pytest.fixture
def app_factory(settings):
    """Build the app over `settings` with the fake Prometheus wired in.
    A factory so restart tests can assemble a second app on the same DB."""

    def make():
        application = create_app(settings)
        runtime = application.state.runtime
        fake_prom = FakeProm()
        runtime.prom = fake_prom
        runtime.investigator._prom = fake_prom
        return application

    return make


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def firing_payload():
    return json.loads((FIXTURES / "alertmanager_firing.json").read_text())


@pytest.fixture
def resolved_payload():
    return json.loads((FIXTURES / "alertmanager_resolved.json").read_text())
