"""
scheduler.py

Voert een ChargingPlan uit als runtime planning.

Architectuur:

    EVPlanner
        ↓
    ChargingPlan
        ↓
    ChargingDecision
        ↓
    Hour
        ↓
    EVScheduler
        ↓
    RuntimeStatus


Verantwoordelijkheden:

- een ChargingPlan accepteren
- bepalen welk Hour momenteel actief is
- bepalen of laden volgens de planning toegestaan is
- RuntimeStatus bijwerken
- wijzigingen in het actieve laadvenster signaleren
- geselecteerde laaduren beschikbaar stellen


Niet verantwoordelijk voor:

- prijsberekeningen
- Solcast-berekeningen
- optimalisatie
- het maken van een laadplan
- daadwerkelijke laadstroom
- fasekeuze


Pyscript-compatibele versie.

Belangrijk:
- geen @property
- geen list comprehensions
- geen generator expressions
- alleen gewone methodes
- expliciete numerieke conversies
"""


from __future__ import annotations

from datetime import datetime

from .logger import Logger

from .models import (
    Hour,
    RuntimeStatus,
)

from .planner import (
    ChargingPlan,
)


##############################################################################
# Scheduler instellingen
##############################################################################


class SchedulerSettings:
    """
    Instellingen voor de scheduler.
    """

    def __init__(
        self,
        min_charge_energy: float = 0.01,
    ):
        """
        Maakt schedulerinstellingen.

        min_charge_energy:
            Minimale hoeveelheid energie die in een laadvenster
            aanwezig moet zijn voordat laden wordt toegestaan.
        """

        min_charge_energy = float(
            min_charge_energy
        )

        if min_charge_energy < 0:

            raise ValueError(
                "min_charge_energy mag niet negatief zijn."
            )

        self.min_charge_energy = (
            min_charge_energy
        )


##############################################################################
# EV Scheduler
##############################################################################


class EVScheduler:
    """
    Runtime scheduler van een ChargingPlan.

    De scheduler bepaalt alleen:

    - welk laadvenster actief is
    - of laden volgens de planning toegestaan is
    - welke Hour-objecten geselecteerd zijn
    - welke laadinstellingen voor het actieve uur gelden

    De scheduler stuurt zelf geen laadpaal aan.
    """

    ##########################################################################
    # Constructor
    ##########################################################################

    def __init__(
        self,
        settings: SchedulerSettings,
        logger: Logger,
    ):
        """
        Maakt een EVScheduler.
        """

        if not isinstance(
            settings,
            SchedulerSettings,
        ):

            raise TypeError(
                "settings moet een SchedulerSettings zijn."
            )

        self.settings = settings

        self.logger = logger

        ######################################################################
        # Actuele ChargingPlan
        ######################################################################

        self.plan = None

        ######################################################################
        # Runtime status
        ######################################################################

        self.status = RuntimeStatus()

        ######################################################################
        # Laatst actieve Hour
        ######################################################################

        self.active_hour = None

    ##########################################################################
    # Planning instellen
    ##########################################################################

    def set_plan(
        self,
        plan: ChargingPlan,
    ) -> None:
        """
        Stelt een nieuwe ChargingPlan in.

        Een nieuwe planning maakt het huidige actieve uur ongeldig.
        Bij de eerstvolgende update wordt het juiste actieve uur
        opnieuw bepaald.
        """

        if not isinstance(
            plan,
            ChargingPlan,
        ):

            raise TypeError(
                "plan moet een ChargingPlan zijn."
            )

        self.plan = plan

        ######################################################################
        # Actief uur resetten
        ######################################################################

        self.active_hour = None

        self.status.current_hour = None

        self.status.charging_allowed = False

        self.status.charge_power_w = 0.0

        self.status.charge_current_a = 0.0

        self.status.phases = 0

        ######################################################################
        # Basisinformatie loggen
        ######################################################################

        self.logger.debug(
            "Nieuwe EV laadplanning ingesteld."
        )

        self.logger.debug(
            f"Benodigd: "
            f"{float(plan.energy_needed_kwh):.2f} kWh"
        )

        self.logger.debug(
            f"Gepland: "
            f"{float(plan.energy_planned_kwh):.2f} kWh"
        )

        self.logger.debug(
            f"Gratis PV: "
            f"{float(plan.free_energy_kwh):.2f} kWh"
        )

        self.logger.debug(
            f"Betaald: "
            f"{float(plan.paid_energy_kwh):.2f} kWh"
        )

        self.logger.debug(
            f"Geschatte kosten: "
            f"€{float(plan.estimated_cost):.2f}"
        )

        self.logger.debug(
            f"Compleet: "
            f"{plan.complete}"
        )

        self.logger.debug(
            f"Vertrek: "
            f"{plan.departure_time}"
        )

        self.logger.debug(
            f"Max prijs: "
            f"€{float(plan.max_price):.3f}/kWh"
        )

        ######################################################################
        # Ontbrekende energie
        ######################################################################

        if float(
            plan.missing_energy_kwh
        ) > 0:

            self.logger.debug(
                f"Ontbrekende energie: "
                f"{float(plan.missing_energy_kwh):.2f} kWh"
            )

    ##########################################################################
    # Planning verwijderen
    ##########################################################################

    def clear_plan(
        self,
    ) -> None:
        """
        Verwijdert de huidige ChargingPlan.
        """

        self.plan = None

        self.active_hour = None

        self.status.current_hour = None

        self.status.charging_allowed = False

        self.status.charge_power_w = 0.0

        self.status.charge_current_a = 0.0

        self.status.phases = 0

        self.logger.debug(
            "EV laadplanning verwijderd."
        )

    ##########################################################################
    # Planning aanwezig?
    ##########################################################################

    def has_plan(
        self,
    ) -> bool:
        """
        Geeft True terug wanneer een ChargingPlan aanwezig is.

        Gewone methode in plaats van @property vanwege Pyscript.
        """

        return self.plan is not None

    ##########################################################################
    # Actief uur bepalen
    ##########################################################################

    def get_current_hour(
        self,
        now: datetime,
    ):
        """
        Geeft het momenteel actieve geselecteerde Hour terug.

        Alleen geselecteerde laaduren kunnen actief worden.

        Tijdgrens:

            hour.start <= now < hour.end
        """

        if self.plan is None:

            return None

        ######################################################################
        # ChargingPlan bevat ChargingDecision-objecten.
        #
        # Het Hour-object zit in decision.hour.
        ######################################################################

        for decision in self.plan.decisions:

            if not decision.selected:

                continue

            hour = decision.hour

            if (
                hour.start
                <= now
                < hour.end
            ):

                return hour

        return None

    ##########################################################################
    # Laden toegestaan?
    ##########################################################################

    def charging_allowed(
        self,
        now: datetime,
    ) -> bool:
        """
        Bepaalt of volgens de huidige planning geladen mag worden.

        Laden is toegestaan wanneer:

        1. een geselecteerd laadvenster actief is
        2. de hoeveelheid laadenergie boven de minimale grens ligt
        """

        hour = self.get_current_hour(
            now
        )

        if hour is None:

            return False

        charge_energy = float(
            hour.charge_energy
        )

        minimum = float(
            self.settings.min_charge_energy
        )

        return (
            charge_energy
            >= minimum
        )

    ##########################################################################
    # Runtime laadinstellingen synchroniseren
    ##########################################################################

    def _update_runtime_charge_settings(
        self,
        hour,
    ) -> None:
        """
        Synchroniseert de laadinstellingen van het actieve Hour
        naar RuntimeStatus.

        De scheduler berekent hier niets opnieuw.

        De waarden komen rechtstreeks uit het laadplan:

            Hour.charge_power_w
            Hour.charge_current_a
            Hour.phases

        De daadwerkelijke laadpaal wordt hier niet aangestuurd.
        """

        ######################################################################
        # Geen actief uur
        ######################################################################

        if hour is None:

            self.status.charge_power_w = 0.0

            self.status.charge_current_a = 0.0

            self.status.phases = 0

            return

        ######################################################################
        # Actief uur
        ######################################################################

        self.status.charge_power_w = float(
            hour.charge_power_w
        )

        self.status.charge_current_a = float(
            hour.charge_current_a
        )

        self.status.phases = int(
            hour.phases
        )

    ##########################################################################
    # Update
    ##########################################################################

    def update(
        self,
        now: datetime,
    ) -> bool:
        """
        Werkt de scheduler bij.

        Retourneert:

            True
                wanneer het actieve laadvenster veranderd is.

            False
                wanneer het actieve laadvenster hetzelfde is gebleven.
        """

        ######################################################################
        # Geen planning
        ######################################################################

        if self.plan is None:

            changed = (
                self.active_hour
                is not None
            )

            self.active_hour = None

            self.status.current_hour = None

            self.status.charging_allowed = False

            self._update_runtime_charge_settings(
                None
            )

            return changed

        ######################################################################
        # Huidig Hour bepalen
        ######################################################################

        hour = self.get_current_hour(
            now
        )

        ######################################################################
        # Controleren of Hour gewijzigd is
        ######################################################################

        changed = (
            self.active_hour
            is not hour
        )

        ######################################################################
        # Actief Hour opslaan
        ######################################################################

        self.active_hour = hour

        self.status.current_hour = hour

        ######################################################################
        # Runtime laadinstellingen synchroniseren
        ######################################################################

        self._update_runtime_charge_settings(
            hour
        )

        ######################################################################
        # Laden toestaan
        ######################################################################

        if hour is None:

            self.status.charging_allowed = False

        else:

            charge_energy = float(
                hour.charge_energy
            )

            minimum = float(
                self.settings.min_charge_energy
            )

            self.status.charging_allowed = (
                charge_energy
                >= minimum
            )

        ######################################################################
        # Alleen loggen wanneer het actieve venster verandert
        ######################################################################

        if changed:

            ##################################################################
            # Geen actief laadvenster
            ##################################################################

            if hour is None:

                self.logger.debug(
                    "Geen actief laadvenster."
                )

            ##################################################################
            # Nieuw actief laadvenster
            ##################################################################

            else:

                self.logger.debug(
                    "Nieuw laadvenster actief: "
                    f"{hour.start:%d-%m %H:%M}"
                    f" - "
                    f"{hour.end:%H:%M}"
                )

                self.logger.debug(
                    f"Energie: "
                    f"{float(hour.charge_energy):.2f} kWh"
                )

                self.logger.debug(
                    f"Gratis PV: "
                    f"{float(hour.free_energy):.2f} kWh"
                )

                self.logger.debug(
                    f"Betaald: "
                    f"{float(hour.paid_energy):.2f} kWh"
                )

                self.logger.debug(
                    f"Prijs: "
                    f"€{float(hour.price):.3f}/kWh"
                )

                self.logger.debug(
                    f"Laadvermogen: "
                    f"{float(hour.charge_power_w):.0f} W"
                )

                self.logger.debug(
                    f"Laadstroom: "
                    f"{float(hour.charge_current_a):.0f} A"
                )

                self.logger.debug(
                    f"Fasen: "
                    f"{int(hour.phases)}"
                )

                self.logger.debug(
                    f"Reden: "
                    f"{hour.reason}"
                )

                self.logger.debug(
                    f"Laden toegestaan: "
                    f"{self.status.charging_allowed}"
                )

        return changed

    ##########################################################################
    # Actief uur
    ##########################################################################

    def current_hour(
        self,
    ):
        """
        Geeft het momenteel actieve Hour terug.

        Gewone methode in plaats van @property vanwege Pyscript.
        """

        return self.active_hour

    ##########################################################################
    # Geselecteerde uren
    ##########################################################################

    def selected_hours(
        self,
    ) -> list[Hour]:
        """
        Geeft alle geselecteerde laaduren terug.

        Gebruikt bewust een gewone for-lus in plaats van
        een list comprehension.
        """

        result = []

        if self.plan is None:

            return result

        for decision in self.plan.decisions:

            if not decision.selected:

                continue

            result.append(
                decision.hour
            )

        return result

    ##########################################################################
    # Volgende geselecteerde uur
    ##########################################################################

    def next_selected_hour(
        self,
        now: datetime,
    ):
        """
        Geeft het eerstvolgende geselecteerde laadvenster terug.

        Alleen uren waarvan:

            hour.start > now

        gelden als toekomstige uren.
        """

        hours = self.selected_hours()

        for hour in hours:

            if hour.start > now:

                return hour

        return None

    ##########################################################################
    # Runtime status
    ##########################################################################

    def get_status(
        self,
    ) -> RuntimeStatus:
        """
        Geeft de actuele RuntimeStatus terug.
        """

        return self.status