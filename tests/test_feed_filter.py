"""Feed hiring-post filter tests."""

from outreach.ingest.sources.feed_filter import (
    build_feed_needles,
    has_job_url,
    looks_like_hiring_post,
)
from outreach.ingest.sources.linkedin_dms import (
    _classify_conversation,
    _message_to_raw_post,
    _name_matches,
)


def test_looks_like_hiring_with_role() -> None:
    text = "We are expanding the team! #hiring React developers in Bangalore. DM me."
    needles = build_feed_needles([], ["react developer"])
    assert looks_like_hiring_post(text, needles)


def test_rejects_hiring_wrong_role() -> None:
    """Feed must not keep Customer Support just because it says hiring."""
    text = (
        "We are hiring Customer Support Executives for our call center. "
        "Apply now if you have excellent communication skills."
    )
    needles = build_feed_needles(["hiring"], ["full stack", "react developer", "node.js"])
    assert not looks_like_hiring_post(text, needles, require_role=True)


def test_rejects_non_hiring_short() -> None:
    assert not looks_like_hiring_post("Nice sunset today", ["hiring"])


def test_role_needle_match() -> None:
    text = (
        "Our startup needs a full stack engineer comfortable with Node.js and React. "
        "Reach out if interested in joining."
    )
    needles = build_feed_needles(["open role"], ["full stack", "react"])
    assert looks_like_hiring_post(text, needles)


def test_job_url_with_role() -> None:
    assert has_job_url("see https://www.linkedin.com/jobs/view/12345/")
    text = "Full stack role open https://boards.greenhouse.io/acme/jobs/99"
    assert looks_like_hiring_post(text, build_feed_needles([], ["full stack"]))


def test_dm_loose_mode_accepts_hiring_without_role() -> None:
    text = "Hey check this opening we are hiring for our team — apply here please thanks"
    needles = build_feed_needles(["hiring"], ["full stack"])
    assert looks_like_hiring_post(text, needles, require_role=False)


def test_name_matches_partial() -> None:
    assert _name_matches("Abhishek Kumar", "Abhishek Kumar")
    assert _name_matches("Abhishek Kumar · 2nd", "Abhishek")


def test_classify_bookmark_and_inbound() -> None:
    needles = build_feed_needles(["hiring"], ["react"])
    assert (
        _classify_conversation(
            "Abhishek Kumar",
            "hey",
            bookmark_contacts=["Abhishek Kumar"],
            inbound_contacts=[],
            needles=needles,
            scan_recent_if_no_inbound=True,
        )
        == "bookmark"
    )
    assert (
        _classify_conversation(
            "Random Friend",
            "We're hiring a React developer — apply here",
            bookmark_contacts=["Abhishek Kumar"],
            inbound_contacts=[],
            needles=needles,
            scan_recent_if_no_inbound=True,
        )
        == "inbound"
    )
    assert (
        _classify_conversation(
            "Random Friend",
            "lunch tomorrow?",
            bookmark_contacts=["Abhishek Kumar"],
            inbound_contacts=[],
            needles=needles,
            scan_recent_if_no_inbound=True,
        )
        is None
    )


def test_message_to_raw_post_keeps_dm_metadata() -> None:
    post = _message_to_raw_post(
        contact="Abhishek Kumar",
        dm_kind="bookmark",
        text="Hiring full stack — https://www.linkedin.com/jobs/view/999/",
        links=["https://www.linkedin.com/jobs/view/999/"],
    )
    assert post is not None
    assert post.source == "linkedin_dms"
    assert post.extra["dm_kind"] == "bookmark"
    assert "does not mean applied" in post.raw_text
    assert "linkedin.com/jobs/view/999" in post.url
