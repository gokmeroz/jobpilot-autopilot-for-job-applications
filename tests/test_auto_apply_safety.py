from __future__ import annotations

from src.apply.forms.ashby_form import _answer_custom_field as ashby_answer
from src.apply.forms.greenhouse_form import _answer_custom_question as greenhouse_answer
from src.models import Route
from src.pipeline import _assign_plan
from tests.conftest import make_job


class FakeSelect:
    def __init__(self) -> None:
        self.selected: str | None = None

    def evaluate(self, script: str):
        if "tagName" in script:
            return "select"
        if "Array.from(e.options)" in script:
            return [
                {"v": "", "t": "Select..."},
                {"v": "first", "t": "First real option"},
            ]
        return None

    def get_attribute(self, name: str) -> str | None:
        return None

    def select_option(self, *, value: str | None = None, label: str | None = None) -> None:
        self.selected = value or label


def _cfg(**overrides) -> dict:
    cfg = {
        "apply": {
            "cover_letter_short": "short.md",
            "cover_letter_long": "long.md",
            "allow_select_fallback": False,
        },
    }
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


def test_assign_plan_routes_known_filler_ats_to_auto():
    job = make_job(ats="ashby", source_tier=1)
    planned = _assign_plan(job, _cfg())
    assert planned.route == Route.auto


def test_assign_plan_routes_unknown_ats_to_manual():
    job = make_job(ats="unknownats", source_tier=1)
    planned = _assign_plan(job, _cfg())
    assert planned.route == Route.manual


def test_unknown_ashby_select_is_not_guessed_by_default():
    el = FakeSelect()
    handled = ashby_answer(el, "select", "Unrecognized dropdown", None, make_job(), _cfg())
    assert handled is False
    assert el.selected is None


def test_unknown_ashby_select_fallback_is_opt_in():
    el = FakeSelect()
    cfg = _cfg(apply={**_cfg()["apply"], "allow_select_fallback": True})
    handled = ashby_answer(el, "select", "Unrecognized dropdown", None, make_job(), cfg)
    assert handled is True
    assert el.selected == "first"


def test_unknown_greenhouse_select_is_not_guessed_by_default():
    el = FakeSelect()
    handled = greenhouse_answer("Unrecognized dropdown", el, None, make_job(), cfg=_cfg())
    assert handled is False
    assert el.selected is None
