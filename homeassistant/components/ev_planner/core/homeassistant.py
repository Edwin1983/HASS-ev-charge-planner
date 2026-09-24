"""
homeassistant.py

Dunne koppellaag tussen Python en Home Assistant.

Verantwoordelijkheden:

- Home Assistant states uitlezen
- Home Assistant attributes uitlezen
- Home Assistant state-objecten uitlezen

Niet verantwoordelijk voor:

- prijsberekeningen
- Solcast-berekeningen
- planning
- optimalisatie
- PlannerSettings
- scheduler-logica
- statuslogica
- laadstrategie

De overige modules werken uitsluitend met Python-objecten.

Alle Home Assistant-specifieke communicatie blijft in deze module.

Deze wrapper is uitsluitend lezend. De controller stuurt geen
charger of andere entities aan; waar de integratie ooit een
service moet aanroepen, gebeurt dat rechtstreeks via de native
`hass.services`/`hass.states` API in de HA-laag (`__init__.py`),
niet via deze module.
"""

from __future__ import annotations

from typing import Any

from .logger import Logger


##############################################################################
# Home Assistant
##############################################################################


class HomeAssistant:
    """
    Dunne, alleen-lezen wrapper rond Home Assistant.
    """

    ##########################################################################
    # Constructor
    ##########################################################################

    def __init__(
        self,
        hass: Any,
        logger: Logger,
    ):
        """
        Initialiseert de Home Assistant koppeling.
        """

        self.hass = hass
        self.logger = logger

    ##########################################################################
    # State uitlezen
    ##########################################################################

    def get_state(
        self,
        entity_id: str,
        attribute: str | None = None,
    ) -> Any:
        """
        Leest de state van een Home Assistant entity.
        """

        try:

            state = self.hass.states.get(
                entity_id
            )

        except Exception as err:

            self.logger.error(
                "Fout bij uitlezen van "
                f"{entity_id}: {err}"
            )

            return None

        if state is None:

            self.logger.warning(
                "Entity niet gevonden: "
                f"{entity_id}"
            )

            return None

        if attribute is not None:

            try:

                return state.attributes.get(
                    attribute
                )

            except (
                AttributeError,
                TypeError,
            ):

                self.logger.warning(
                    "Ongeldige attributes voor "
                    f"{entity_id}"
                )

                return None

        return state.state

    ##########################################################################
    # Attributes uitlezen
    ##########################################################################

    def get_attributes(
        self,
        entity_id: str,
    ) -> dict:
        """
        Leest de attributes van een Home Assistant entity.
        """

        try:

            state = self.hass.states.get(
                entity_id
            )

        except Exception as err:

            self.logger.error(
                "Fout bij uitlezen van "
                f"{entity_id}: {err}"
            )

            return {}

        if state is None:

            return {}

        try:

            return dict(
                state.attributes
            )

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):

            self.logger.warning(
                "Ongeldige attributes voor "
                f"{entity_id}"
            )

            return {}

    ##########################################################################
    # Volledig state-object
    ##########################################################################

    def get_state_object(
        self,
        entity_id: str,
    ) -> Any | None:
        """
        Geeft het volledige Home Assistant state-object terug.
        """

        try:

            return self.hass.states.get(
                entity_id
            )

        except Exception as err:

            self.logger.error(
                "Fout bij uitlezen van "
                f"{entity_id}: {err}"
            )

            return None
