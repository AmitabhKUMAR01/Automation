"""Experience window: roles asking <= N years are scored on fit, not years gap."""

from outreach.match.stage import experience_policy, min_years_required


def test_min_years_parsing() -> None:
    assert min_years_required("3-5 years", None) == 3
    assert min_years_required(None, "Looking for 4+ yrs React devs") == 4
    assert min_years_required("1 to 4 years", None) == 1
    assert min_years_required(None, "Freshers welcome, React role") == 0
    assert min_years_required(None, "Hiring React developer, mail hr@x.com") is None
    assert min_years_required("5-8 years", "2 years in Node preferred") == 5


def test_policy_inside_window_ignores_gap() -> None:
    assert "do NOT lower the score" in experience_policy(3, 4)
    assert "do NOT lower the score" in experience_policy(4, 4)


def test_policy_outside_window_stays_strict() -> None:
    assert experience_policy(6, 4) == ""


def test_policy_unknown_years_is_lenient() -> None:
    assert "no clear years requirement" in experience_policy(None, 4)
