"""The rules card: rendered from the canonical spec, readable, and faithful to names."""

import copy

import pytest

from engine.spec import validate_spec
from engine.spec.card import render_card
from engine.spec.examples import EXAMPLES, OPENING_RANGE_LONG


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_every_example_renders(name):
    card = render_card(validate_spec(EXAMPLES[name]))
    assert set(card["sections"]) == {"direction", "filters", "setup", "entry", "stop", "target", "limits"}
    assert all(card["sections"].values()) and "None" not in card["text"]


def test_names_keep_their_case():
    d = copy.deepcopy(OPENING_RANGE_LONG)
    d["target"] = {"type": "level", "at": {"block": "window_high_low", "params": {"window": "ny_am"}, "output": "high"}}
    assert render_card(validate_spec(d))["sections"]["target"] == "The NY AM session high."


def test_days_read_as_a_choice():
    d = copy.deepcopy(OPENING_RANGE_LONG)
    d["filters"] = [{"event": {"block": "day_of_week", "params": {"days": ["thu", "tue", "wed"]}}}]
    assert render_card(validate_spec(d))["sections"]["filters"] == "Only on Tuesday, Wednesday or Thursday."
    d["filters"] = [{"event": {"block": "day_of_week", "params": {"days": ["fri"]}}}]
    assert render_card(validate_spec(d))["sections"]["filters"] == "Only on Friday."
