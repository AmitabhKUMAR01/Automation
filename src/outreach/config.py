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


class NaukriIngestConfig(BaseModel):
    """Naukri job discovery only — never auto-applies."""

    storage_state: str = "credentials/naukri_storage_state.json"
    headless: bool = False
    locations: list[str] = Field(
        default_factory=lambda: ["delhi-ncr", "remote"],
    )
    keywords: list[str] = Field(
        default_factory=lambda: [
            "full stack developer",
            "react developer",
            "node.js developer",
        ],
    )
    experience_years: str = "2"  # passed into search UX when possible
    max_pages_per_query: int = 2
    max_jobs_per_run: int = 40
    scroll_pause_min: float = 2.0
    scroll_pause_max: float = 4.0
    shortlist_path: str = "data/naukri_shortlist.md"
    profile_summary_path: str = "Naukri.com.txt"


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
    # Keep scrolling (up to feed_max_scrolls_hard) until this many posts contain an email
    feed_min_with_email: int = 5
    feed_max_scrolls_hard: int = 40
    # Wall-clock budget for the feed scroll loop (whichever of scrolls/minutes hits first)
    feed_max_minutes: float = 10.0
    # Consecutive scrolls with no new cards before we try to load more / refresh the feed
    feed_stall_scrolls: int = 4
    feed_scroll_pause_min: float = 2.0
    feed_scroll_pause_max: float = 4.5
    feed_navigation_timeout_ms: int = 60000
    # LinkedIn Messaging (Playwright) — opening a chat never marks applied
    dm: DmIngestConfig = Field(default_factory=DmIngestConfig)
    # Naukri discovery (Playwright) — shortlist only, never auto-apply
    naukri: NaukriIngestConfig = Field(default_factory=NaukriIngestConfig)
    # Apify post search: several short queries beat one OR-tree; empty = auto keyword
    post_search_queries: list[str] = Field(default_factory=list)
    post_search_per_query: int = 25
    post_search_max_total: int = 100


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
    # Posts whose minimum required years <= this are scored on role/stack fit only
    # (no penalty for the years gap). Above it, experience counts as usual.
    experience_flex_max_years: int = 4


class ComposeConfig(BaseModel):
    max_words: int = 150
    your_name: str = "Your Name"
    your_headline: str = ""


class SendConfig(BaseModel):
    require_approval: bool = True
    resume_attachment_path: str = "resume/resume.pdf"
    max_sends_per_day: int = 100
    delay_seconds_min: int = 45
    delay_seconds_max: int = 180
    timezone: str = "Asia/Kolkata"
    business_hours: BusinessHours = Field(default_factory=BusinessHours)
    weekdays_only: bool = True
    email_cooldown_days: int = 60


class ProspectsConfig(BaseModel):
    """Connection requests to HR / leaders / seniors found via LinkedIn people search."""

    daily_cap: int = 15
    weekly_cap: int = 80
    delay_seconds_min: int = 60
    delay_seconds_max: int = 180
    # LinkedIn network filter: S = 2nd degree, O = 3rd+
    network: list[str] = Field(default_factory=lambda: ["S"])
    locations: list[str] = Field(default_factory=lambda: ["Noida", "Delhi", "Gurugram"])
    # A result is kept only if its location line contains one of these
    location_aliases: list[str] = Field(
        default_factory=lambda: ["noida", "delhi", "gurgaon", "gurugram", "ncr"]
    )
    titles: list[str] = Field(
        default_factory=lambda: [
            "HR",
            "HR Recruiter",
            "Talent Acquisition",
            "Technical Recruiter",
            "IT Recruiter",
        ]
    )
    # Headline categories allowed to be invited (hr | leader | senior)
    allowed_categories: list[str] = Field(default_factory=lambda: ["hr"])
    # Whole-word match against the headline; people search has no company-size filter
    exclude_large_companies: list[str] = Field(default_factory=list)
    # titles x locations can be 30+ searches; rotate a few per run to keep page views low
    max_queries_per_run: int = 6
    max_pages_per_query: int = 2
    max_new_per_run: int = 60


class NetworkConfig(BaseModel):
    """LinkedIn connection outreach (review-gated, paced). Uses send.business_hours."""

    prospects: ProspectsConfig = Field(default_factory=ProspectsConfig)

    daily_cap: int = 15
    delay_seconds_min: int = 120
    delay_seconds_max: int = 300
    headless: bool = False
    recent_days: int = 14
    max_connections_scan: int = 400
    max_profile_checks: int = 25
    min_years: int = 4
    exclude_companies: list[str] = Field(
        default_factory=lambda: ["hangingpanda", "hanging panda"]
    )
    template_recent: str = (
        "Hi {first_name}, we just connected, and I'm already asking for something. 😅 "
        "I'm currently looking for a Full-Stack Developer role and wanted to check if there "
        "are any relevant opportunities available in {org}. Happy to share my resume if that helps!"
    )
    template_older: str = (
        "Hi {first_name}, hope you're doing well! I'm currently looking for a Full-Stack "
        "Developer role (React / Next.js / Node.js) and wanted to check if there are any "
        "relevant opportunities at {org}. Happy to share my resume if that helps!"
    )


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
    network: NetworkConfig = Field(default_factory=NetworkConfig)
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
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    )
    anthropic_model: str = "claude-3-5-haiku-latest"
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        validation_alias=AliasChoices("ANTHROPIC_BASE_URL"),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    # Alias tracks the current Flash model; pinned versions get retired for new keys
    gemini_model: str = "gemini-flash-latest"
    # Tried in order when a model is overloaded (503) or retired; each has its own free quota
    gemini_models: str = "gemini-flash-latest,gemini-3.5-flash,gemini-flash-lite-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Ordered fallback, e.g. "openai,gemini". Empty = single LLM_PROVIDER.
    llm_providers: str | None = None

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
