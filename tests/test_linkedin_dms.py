"""DM conversation selection."""

from outreach.ingest.sources.linkedin_dms import _classify_conversation, _message_to_raw_post


def _classify(name: str, preview: str) -> str | None:
    return _classify_conversation(
        name,
        preview,
        bookmark_contacts=["Abhishek Kumar"],
        inbound_contacts=[],
        needles=[],
        scan_recent_if_no_inbound=True,
    )


def test_shared_post_preview_is_opened() -> None:
    assert _classify("Ankit Singh", "Ankit sent a post") == "inbound"
    assert _classify("Shiv Shankar", "You sent a post") == "inbound"


def test_casual_chat_is_skipped() -> None:
    assert _classify("Abdul Baroon", "haha see you tomorrow") is None


def test_bookmark_wins() -> None:
    assert _classify("Abhishek Kumar", "You sent a post") == "bookmark"


def test_shared_hiring_card_becomes_post() -> None:
    text = (
        "Ankit Singh sent the following message at 9:10 AM\n"
        "We're hiring a Full Stack Developer (React, Node.js).\n"
        "Please share your updated CV at pragyashree.jain@vayuz.com"
    )
    post = _message_to_raw_post(contact="Ankit Singh", dm_kind="inbound", text=text, links=[])
    assert post is not None
    assert "pragyashree.jain@vayuz.com" in post.raw_text
