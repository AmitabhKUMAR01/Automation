"""Feed hiring-post filter tests."""

from outreach.ingest.sources.feed_filter import build_feed_needles, looks_like_hiring_post


def test_looks_like_hiring_with_hash() -> None:
    text = "We are expanding the team! #hiring React developers in Bangalore. DM me."
    assert looks_like_hiring_post(text, build_feed_needles([], ["react developer"]))


def test_rejects_non_hiring_short() -> None:
    assert not looks_like_hiring_post("Nice sunset today", ["hiring"])


def test_role_needle_match() -> None:
    text = (
        "Our startup needs a full stack engineer comfortable with Node.js and React. "
        "Reach out if interested in joining."
    )
    needles = build_feed_needles(["open role"], ["full stack", "react"])
    assert looks_like_hiring_post(text, needles)
