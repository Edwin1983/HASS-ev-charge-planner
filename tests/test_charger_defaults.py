"""Voorinvulling van de laderfelden in het opties-formulier."""

from __future__ import annotations

from custom_components.ev_planner import const
from custom_components.ev_planner.config_flow import CHARGER_KEYS, _charger_fields


def _suggested(fields):
    return {
        str(marker.schema): (marker.description or {}).get("suggested_value")
        for marker in fields
    }


def test_first_time_the_form_is_prefilled_from_the_automation():
    suggested = _suggested(_charger_fields({}))
    assert suggested[const.CONF_CHARGER_MODE_ENTITY] == "sensor.ev_charger_status"
    assert (
        suggested[const.CONF_CHARGER_CURRENT_ENTITY]
        == "number.ev_charger_max_current"
    )
    assert suggested[const.CONF_CHARGER_START_ENTITY] == "switch.ev_charger_charging"
    assert (
        suggested[const.CONF_CHARGER_STOP_ENTITY]
        == "button.ev_charger_stop_charging"
    )
    assert suggested[const.CONF_CHARGER_PHASE_ENTITY].endswith("phase_switch")
    assert suggested[const.CONF_CHARGER_PHASE_ONE_VALUE] == 32
    assert suggested[const.CONF_CHARGER_PHASE_THREE_VALUE] == 0
    assert (
        suggested[const.CONF_CHARGER_ENERGY_ENTITY]
        == "sensor.ev_charger_session_total_charge"
    )


def test_saved_values_win_and_cleared_fields_stay_empty():
    saved = {
        const.CONF_CHARGER_MODE_ENTITY: "sensor.other",
        const.CONF_CHARGER_CURRENT_ENTITY: "number.other",
    }
    suggested = _suggested(_charger_fields(saved))
    assert suggested[const.CONF_CHARGER_MODE_ENTITY] == "sensor.other"
    assert suggested[const.CONF_CHARGER_PHASE_ENTITY] is None
    assert suggested[const.CONF_CHARGER_START_ENTITY] is None


def test_defaults_cover_every_field():
    assert set(const.CHARGER_DEFAULTS) == set(CHARGER_KEYS)
