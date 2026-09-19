"""Load YAML config + environment secrets into typed settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class BusinessHours(BaseModel):
    start: str = "09:00"
    end: str = "18:00"


class DmIngestConfig(BaseModel):
    """LinkedIn Messaging watchlist — bookmark + inbound job DMs (read-only)."""

    bookmark_contacts: list[str] = Field(
        default_factory=lambda: ["Abhishek Kumar"],
        description="Chats you use as a job bookmark (e.g. forward openings here)",
    )
    inbound_contacts: list[str] = Field(
        default_factory=list,
        description="Friend names to always scan; empty = also scan recent hiring-looking threads",
    )
    max_conversations: int = 15
    max_messages_per_thread: int = 40
    lookback_days: int = 30
    scan_recent_if_no_inbound: bool = True


class IngestConfig(BaseModel):
    source: str = "apify"
    lookback_days: int = 7
    search_terms: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    max_posts_per_run: int = 50
    csv_path: str = "fixtures/sample_posts.csv"
    # LinkedIn personal feed (Playwright)
    linkedin_storage_state: str = "credentials/linkedin_storage_state.json"
    linkedin_headless: bool = False  # visible browser is safer for LinkedIn challenges
    feed_max_scrolls: int = 12
    feed_scroll_pause_min: float = 2.0
    feed_scroll_pause_max: float = 4.5
    feed_navigation_timeout_ms: int = 60000
    # LinkedIn Messaging (Playwright) — opening a chat never marks applied
    dm: DmIngestConfig = Field(default_factory=DmIngestConfig)


class ParseConfig(BaseModel):
    llm_retries: int = 1


class EnrichConfig(BaseModel):
    enabled: bool = False
    provider: str = "hunter"
    min_confidence: float = 0.7
    target_titles: list[str] = Field(default_factory=list)


class MatchConfig(BaseModel):
    resume_path: str = "resume/resume.pdf"
    score_cutoff: int = 60


class ComposeConfig(BaseModel):
    max_words: int = 150
    your_name: str = "Your Name"
    your_headline: str = ""


class SendConfig(BaseModel):
    require_approval: bool = True
    resume_attachment_path: str = "resume/resume.pdf"
    max_sends_per_day: int = 25
    delay_seconds_min: int = 45
    delay_seconds_max: int = 180
    timezone: str = "Asia/Kolkata"
    business_hours: BusinessHours = Field(default_factory=BusinessHours)
    weekdays_only: bool = True
    email_cooldown_days: int = 60


class LlmYamlConfig(BaseModel):
    temperature: float = 0.2
    timeout_seconds: int = 60


class DatabaseYamlConfig(BaseModel):
    path: str = "data/outreach.db"


class AppConfig(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    parse: ParseConfig = Field(default_factory=ParseConfig)
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    match: MatchConfig = Field(default_factory=MatchConfig)
    compose: ComposeConfig = Field(default_factory=ComposeConfig)
    send: SendConfig = Field(default_factory=SendConfig)
    llm: LlmYamlConfig = Field(default_factory=LlmYamlConfig)
    database: DatabaseYamlConfig = Field(default_factory=DatabaseYamlConfig)


class Secrets(BaseSettings):
    """Secrets and runtime overrides from .env only."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-haiku-latest"

    apify_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("APIFY_API_TOKEN", "APIFY_API_KEY"),
    )
    apify_actor_id: str = "apimaestro/linkedin-posts-search-scraper-no-cookies"

    hunter_api_key: str | None = None
    apollo_api_key: str | None = None

    gmail_client_secrets_path: str = "credentials/gmail_client_secrets.json"
    gmail_token_path: str = "credentials/gmail_token.json"
    gmail_sender: str | None = None

    database_url: str | None = None
    config_path: str = "config.yaml"


class Settings(BaseModel):
    config: AppConfig
    secrets: Secrets
    root_dir: Path = ROOT_DIR

    @property
    def database_url(self) -> str:
        if self.secrets.database_url:
            return self.secrets.database_url
        db_path = (self.root_dir / self.config.database.path).resolve()
        return f"sqlite:///{db_path.as_posix()}"

    def resolve_path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        return (self.root_dir / path).resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


@lru_cache
def get_settings(config_path: str | None = None) -> Settings:
    secrets = Secrets()
    path = Path(config_path or secrets.config_path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    app_config = AppConfig.model_validate(_load_yaml(path))
    return Settings(config=app_config, secrets=secrets, root_dir=ROOT_DIR)


def reload_settings(config_path: str | None = None) -> Settings:
    get_settings.cache_clear()
    return get_settings(config_path)
