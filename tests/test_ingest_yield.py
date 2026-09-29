"""Feed/post-search yield: only *new* emails count, variants pass the filter."""

from outreach.config import reload_settings
from outreach.ingest.known import KnownInventory
from outreach.ingest.sources.apify import ApifyLinkedInSource
from outreach.ingest.sources.feed_filter import build_feed_needles, looks_like_hiring_post


def test_new_emails_ignores_known() -> None:
    inv = KnownInventory(emails={"hr@old.com"})
    text = "We're hiring! Send CV to HR@old.com or jobs@new.io"
    assert inv.new_emails(text) == ["jobs@new.io"]


def test_spelling_variants_pass_feed_filter() -> None:
    settings = reload_settings()
    needles = build_feed_needles(settings.config.ingest.search_terms, settings.config.ingest.target_roles)
    for text in (
        "We are hiring a React JS Developer, 2+ yrs. Mail hr@x.com",
        "Hiring NodeJS engineers for our backend team, apply now",
        "Hiring MERN stack developers in Noida — share CV at a@b.co",
    ):
        assert looks_like_hiring_post(text, needles), text


def test_post_search_dedupes_across_queries_and_db(monkeypatch) -> None:
    settings = reload_settings()
    settings.config.ingest.post_search_queries = ["q1", "q2"]
    settings.secrets.apify_api_token = "t"

    def fake_items(self, token, keyword):
        shared = {"url": "https://linkedin.com/posts/a-activity-1", "text": "hiring full stack dev"}
        old = {"url": "https://linkedin.com/posts/old-activity-2", "text": "hiring react dev"}
        own = {"url": f"https://linkedin.com/posts/{keyword}-activity-3", "text": "hiring node dev"}
        return [shared, old, own]

    monkeypatch.setattr(ApifyLinkedInSource, "_run_query", fake_items)
    monkeypatch.setattr(
        "outreach.ingest.known.load_known_inventory",
        lambda: KnownInventory(urls={"https://linkedin.com/posts/old-activity-2"}),
    )
    posts = ApifyLinkedInSource(settings).fetch()
    urls = sorted(p.url for p in posts)
    assert urls == [
        "https://linkedin.com/posts/a-activity-1",
        "https://linkedin.com/posts/q1-activity-3",
        "https://linkedin.com/posts/q2-activity-3",
    ]
