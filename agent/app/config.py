"""All configuration in one place, read from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM — any OpenAI-compatible endpoint (Ollama, DashScope, OpenRouter, vLLM)
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:8b"
    llm_timeout_s: float = 60.0
    max_tool_calls: int = 10
    agent_mode: str = "live"  # "live" | "stub" (deterministic scripted investigator)

    # Integrations
    slack_webhook_url: str = ""
    prometheus_url: str = "http://prometheus:9090"
    prometheus_job: str = "shopapi"

    # Mounted paths
    repo_path: str = "/repos/shopapi"
    runbooks_dir: str = "/runbooks"
    service_log_path: str = "/var/log/shopapi/app.log"
    db_path: str = "/data/sreagent.db"
    postmortem_dir: str = "/postmortems"
