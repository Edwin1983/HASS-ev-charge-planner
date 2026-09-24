"""
status.py

Statusinformatie voor de EV smart charger.

Verantwoordelijkheden:

- status van de scheduler vertalen naar een eenvoudige status
- actuele laadbeslissing beschikbaar maken
- laadvermogen, laadstroom en fasen beschikbaar maken
- informatie leveren voor Home Assistant / logging

Niet verantwoordelijk voor:

- prijsberekeningen
- Solcast
- planning
- laadstroom bepalen
- fasekeuze
- daadwerkelijk starten/stoppen van de lader

Architectuur:

    ChargingPlan
         │
         ▼
     EVScheduler
         │
         ▼
    EVStatusManager
         │
         ▼
      EVStatus
         │
         ▼
    Home Assistant
"""


from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .logger import Logger
from .scheduler import EVScheduler


##############################################################################
# EV Status
##############################################################################


@dataclass
class EVStatus:
    """
    Momentopname van de huidige EV-plannerstatus.
    """

    ##########################################################################
    # Basisstatus
    ##########################################################################

    active: bool = False

    charging_allowed: bool = False

    ##########################################################################
    # Laadvenster
    ##########################################################################

    start: datetime | None = None

    end: datetime | None = None

    ##########################################################################
    # Energie
    ##########################################################################

    energy_kwh: float = 0.0

    free_energy_kwh: float = 0.0

    paid_energy_kwh: float = 0.0

    ##########################################################################
    # Prijs
    ##########################################################################

    price: float = 0.0

    ##########################################################################
    # Laadinstellingen
    #
    # Deze waarden worden rechtstreeks overgenomen uit Hour.
    #
    # De statusmanager berekent hier niets.
    ##########################################################################

    charge_power_w: float = 0.0

    charge_current_a: float = 0.0

    phases: int = 0

    ##########################################################################
    # Reden
    ##########################################################################

    reason: str = ""

    ##########################################################################
    # Dictionary
    ##########################################################################

    def as_dict(
        self,
    ) -> dict:
        """
        Zet de status om naar een dictionary.

        Deze dictionary is uitsluitend de externe uitvoer richting
        Home Assistant.
        """

        ######################################################################
        # Technische state
        ######################################################################

        if self.active:

            if self.charging_allowed:

                state = "charging"

            else:

                state = "waiting"

        else:

            state = "idle"

        ######################################################################
        # Mensleesbare state
        ######################################################################

        if self.active:

            if self.charging_allowed:

                state_text = "Laden"

            else:

                state_text = "Wachten"

        else:

            state_text = "Wachten"

        ######################################################################
        # Resultaat
        ######################################################################

        return {

            ##################################################################
            # Status
            ##################################################################

            "state": state,

            "state_text": state_text,

            "active": self.active,

            "charging_allowed": (
                self.charging_allowed
            ),

            ##################################################################
            # Laadvenster
            ##################################################################

            "start": (
                self.start.isoformat()
                if self.start is not None
                else None
            ),

            "end": (
                self.end.isoformat()
                if self.end is not None
                else None
            ),

            ##################################################################
            # Energie
            ##################################################################

            "energy_kwh": (
                self.energy_kwh
            ),

            "free_energy_kwh": (
                self.free_energy_kwh
            ),

            "paid_energy_kwh": (
                self.paid_energy_kwh
            ),

            ##################################################################
            # Prijs
            ##################################################################

            "price": (
                self.price
            ),

            ##################################################################
            # Laadinstellingen
            ##################################################################

            "charge_power_w": (
                self.charge_power_w
            ),

            "charge_current_a": (
                self.charge_current_a
            ),

            "phases": (
                self.phases
            ),

            ##################################################################
            # Reden
            ##################################################################

            "reason": (
                self.reason
            ),
        }


##############################################################################
# EV Status Manager
##############################################################################


class EVStatusManager:
    """
    Beheert de actuele runtime-status van de EV planner.
    """

    ##########################################################################
    # Constructor
    ##########################################################################

    def __init__(
        self,
        scheduler: EVScheduler,
        logger: Logger,
    ) -> None:
        """
        Initialiseert de statusmanager.
        """

        self.scheduler = scheduler

        self.logger = logger

        self._status = EVStatus()

    ##########################################################################
    # Status bijwerken
    ##########################################################################

    def update(
        self,
        now: datetime,
    ) -> EVStatus:
        """
        Werkt de status bij aan de hand van de scheduler.

        De scheduler blijft verantwoordelijk voor het bepalen van het
        actieve laadvenster.

        De statusmanager vertaalt dit alleen naar EVStatus.
        """

        ######################################################################
        # Actief uur ophalen
        ######################################################################

        hour = self.scheduler.current_hour()

        ######################################################################
        # Geen actief uur
        ######################################################################

        if hour is None:

            self._status = EVStatus()

            return self._status

        ######################################################################
        # Laden toegestaan
        ######################################################################

        charging_allowed = (
            self.scheduler.charging_allowed(
                now
            )
        )

        ######################################################################
        # Laadinstellingen
        #
        # Rechtstreeks uit Hour.
        #
        # Geen berekening in status.py.
        ######################################################################

        charge_power_w = float(
            hour.charge_power_w
        )

        charge_current_a = float(
            hour.charge_current_a
        )

        phases = int(
            hour.phases
        )

        ######################################################################
        # Status opbouwen
        ######################################################################

        self._status = EVStatus(

            ##################################################################
            # Basisstatus
            ##################################################################

            active=True,

            charging_allowed=(
                charging_allowed
            ),

            ##################################################################
            # Laadvenster
            ##################################################################

            start=(
                hour.start
            ),

            end=(
                hour.end
            ),

            ##################################################################
            # Energie
            ##################################################################

            energy_kwh=(
                float(
                    hour.charge_energy
                )
            ),

            free_energy_kwh=(
                float(
                    hour.free_energy
                )
            ),

            paid_energy_kwh=(
                float(
                    hour.paid_energy
                )
            ),

            ##################################################################
            # Prijs
            ##################################################################

            price=(
                float(
                    hour.price
                )
            ),

            ##################################################################
            # Laadinstellingen
            ##################################################################

            charge_power_w=(
                charge_power_w
            ),

            charge_current_a=(
                charge_current_a
            ),

            phases=(
                phases
            ),

            ##################################################################
            # Reden
            ##################################################################

            reason=(
                hour.reason
            ),
        )

        return self._status

    ##########################################################################
    # Huidige status ophalen
    ##########################################################################

    def get_status(
        self,
    ) -> EVStatus:
        """
        Geeft de laatst berekende status terug.

        Bewust een normale methode en geen @property.

        Dit voorkomt dat Pyscript een property uit een externe module
        als EvalFunc behandelt.
        """

        return self._status

    ##########################################################################
    # Dictionary voor Home Assistant
    ##########################################################################

    def as_dict(
        self,
    ) -> dict:
        """
        Geeft de huidige status als dictionary.
        """

        return self._status.as_dict()

    ##########################################################################
    # Status loggen
    ##########################################################################

    def log_status(
        self,
    ) -> None:
        """
        Schrijft de huidige status naar de logger.
        """

        status = self._status

        ######################################################################
        # Geen actief laadvenster
        ######################################################################

        if not status.active:

            self.logger.debug(
                "EV planner: "
                "geen actief laadvenster."
            )

            return

        ######################################################################
        # Actief laadvenster
        ######################################################################

        self.logger.debug(
            "EV planner: actief laadvenster "
            f"{status.start:%d-%m %H:%M}"
            f" - "
            f"{status.end:%H:%M}"
        )

        ######################################################################
        # Energie
        ######################################################################

        self.logger.debug(
            f"Energie: "
            f"{status.energy_kwh:.2f} kWh"
        )

        self.logger.debug(
            f"Gratis energie: "
            f"{status.free_energy_kwh:.2f} kWh"
        )

        self.logger.debug(
            f"Betaalde energie: "
            f"{status.paid_energy_kwh:.2f} kWh"
        )

        ######################################################################
        # Prijs
        ######################################################################

        self.logger.debug(
            f"Prijs: "
            f"€{status.price:.3f}/kWh"
        )

        ######################################################################
        # Laadinstellingen
        ######################################################################

        self.logger.debug(
            f"Laadvermogen: "
            f"{status.charge_power_w:.0f} W"
        )

        self.logger.debug(
            f"Laadstroom: "
            f"{status.charge_current_a:.0f} A"
        )

        self.logger.debug(
            f"Fasen: "
            f"{status.phases}"
        )

        ######################################################################
        # Reden
        ######################################################################

        self.logger.debug(
            f"Reden: "
            f"{status.reason}"
        )

        ######################################################################
        # Laden toegestaan
        ######################################################################

        self.logger.debug(
            f"Laden toegestaan: "
            f"{status.charging_allowed}"
        )