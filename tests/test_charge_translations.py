"""Elk veld en elke entiteit van de laadregeling heeft een vertaling."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.ev_planner import const

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "ev_planner"
FILES = ("strings.json", "translations/en.json", "translations/nl.json")

ENTITY_KEYS = {
    "sensor": ("charge_control", "energy_deviation"),
    "switch": ("charge_enabled", "charge_dry_run", "charge_feedback"),
}


def _charger_keys():
    return [
        getattr(const, name)
        for name in dir(const)
        if name.startswith("CONF_CHARGER_")
    ]


def test_there_are_charger_options():
    assert len(_charger_keys()) == 11


def test_every_charger_option_is_translated_with_help_text():
    for filename in FILES:
        data = json.loads((BASE / filename).read_text(encoding="utf-8"))
        init = data["options"]["step"]["init"]
        for key in _charger_keys():
            assert key in init["data"], (filename, key)
            assert key in init["data_description"], (filename, key)


def test_every_charge_entity_has_a_name():
    for filename in FILES:
        data = json.loads((BASE / filename).read_text(encoding="utf-8"))
        for platform, keys in ENTITY_KEYS.items():
            for key in keys:
                assert data["entity"][platform][key]["name"], (filename, platform, key)
