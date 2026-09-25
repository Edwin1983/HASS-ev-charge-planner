"""
solcast.py

Leest de Solcast voorspellingen uit Home Assistant.

Deze module kent uitsluitend de PV-voorspelling.

Er wordt hier GEEN rekening gehouden met:

- uurprijzen
- laadplanning
- vertrektijd
- laadvermogen
- laadstrategie

Output:
    SolcastData

Pyscript-compatibele versie.

Belangrijk:
- geen generator expressions
- geen @property
- geen property-aanroepen
- geen list/dict comprehensions waar mogelijk
- expliciete numerieke conversies
- uitsluitend gewone Python-objecten
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import ENTITIES
from .logger import Logger
from .models import Hour, SolcastData


##############################################################################
# Solcast reader
##############################################################################


class SolcastReader:
    """
    Leest en verwerkt de Solcast PV-voorspelling.
    """

    ##########################################################################
    # Constructor
    ##########################################################################

    def __init__(
        self,
        app: Any,
        logger: Logger,
        entity_today: str | None = None,
        entity_tomorrow: str | None = None,
    ) -> None:
        """
        entity_today / entity_tomorrow: de Solcast forecast-sensors.

        Als er geen entity_id wordt meegegeven, valt dit terug op de
        standaardwaarden uit core/config.py (achterwaartse compatibiliteit).
        """

        self.app = app
        self.logger = logger

        self.entity_today = (
            entity_today
            if entity_today is not None
            else ENTITIES["solcast_today"]
        )

        self.entity_tomorrow = (
            entity_tomorrow
            if entity_tomorrow is not None
            else ENTITIES["solcast_tomorrow"]
        )

    ##########################################################################
    # Publieke API
    ##########################################################################

    def read(
        self,
    ) -> SolcastData:

        self.logger.debug(
            "Lezen Solcast voorspelling gestart."
        )

        today = self._read_today()

        tomorrow = self._read_tomorrow()

        today_hours = self._parse_forecast(
            today,
            getattr(self, "_forecast_interval_minutes", 60),
        )

        tomorrow_hours = self._parse_forecast(
            tomorrow,
            getattr(self, "_tomorrow_forecast_interval_minutes", 60),
        )

        hours = self._merge(
            today_hours,
            tomorrow_hours,
        )

        hours = self._remove_duplicates(
            hours
        )

        hours = self._remove_past(
            hours
        )

        hours = self._sort(
            hours
        )

        self._validate_series(
            hours
        )

        data = self._calculate_statistics(
            hours
        )

        self.logger.debug(
            f"{len(data.hours)} Solcast uren geladen."
        )

        return data

    ##########################################################################
    # Vandaag uitlezen
    ##########################################################################

    def _read_today(
        self,
    ) -> list[dict[str, Any]]:

        entity_id = self.entity_today

        attributes = self.app.get_attributes(
            entity_id
        )

        forecast = attributes.get(
            "detailedForecast"
        )

        if forecast is not None:
            self._forecast_interval_minutes = 30
        else:
            forecast = attributes.get(
                "detailedHourly"
            )
            self._forecast_interval_minutes = 60

        if forecast is None:

            raise RuntimeError(
                "Solcast vandaag ontbreekt."
            )

        if not isinstance(
            forecast,
            list,
        ):

            raise RuntimeError(
                "Solcast vandaag is geen lijst."
            )

        self.logger.debug(
            f"{len(forecast)} Solcast records vandaag "
            f"({self._forecast_interval_minutes} minuten)."
        )

        return forecast

    ##########################################################################
    # Morgen uitlezen
    ##########################################################################

    def _read_tomorrow(
        self,
    ) -> list[dict[str, Any]]:

        entity_id = self.entity_tomorrow

        attributes = self.app.get_attributes(
            entity_id
        )

        forecast = attributes.get(
            "detailedForecast"
        )

        if forecast is not None:
            self._tomorrow_forecast_interval_minutes = 30
        else:
            forecast = attributes.get(
                "detailedHourly"
            )
            self._tomorrow_forecast_interval_minutes = 60

        if forecast is None:

            self.logger.warning(
                "Geen Solcast data voor morgen."
            )

            return []

        if not isinstance(
            forecast,
            list,
        ):

            self.logger.warning(
                "Solcast morgen is geen lijst."
            )

            return []

        self.logger.debug(
            f"{len(forecast)} Solcast records morgen "
            f"({self._tomorrow_forecast_interval_minutes} minuten)."
        )

        return forecast

    ##########################################################################
    # Forecast parser
    ##########################################################################

    def _parse_forecast(
        self,
        forecast: list[dict[str, Any]],
        interval_minutes: int,
    ) -> list[Hour]:

        hours = []

        index = 0

        for item in forecast:

            try:

                hour = self._create_hour(
                    item,
                    interval_minutes,
                )

                hours.append(
                    hour
                )

            except Exception as err:

                self.logger.warning(
                    f"Solcast record {index} "
                    f"overgeslagen: "
                    f"{type(err).__name__}: {err}"
                )

                self.logger.warning(
                    f"Solcast record inhoud: {item}"
                )

            index += 1

        return hours

    ##########################################################################
    # Eén Hour-object maken
    ##########################################################################

    def _create_hour(
        self,
        item: dict[str, Any],
        interval_minutes: int = 60,
    ) -> Hour:
        """
        Maakt één Hour-object uit een Solcast-record.

        period_start mag zowel een datetime als een string zijn.

        Voorbeeld datetime:

            datetime(
                2026,
                8,
                17,
                22,
                0,
                tzinfo=...
            )

        Voorbeeld string:

            "2026-08-17T22:00:00+02:00"
        """

        if not isinstance(
            item,
            dict,
        ):

            raise TypeError(
                "Solcast record is geen dictionary."
            )

        ######################################################################
        # Starttijd
        ######################################################################

        raw_period_start = item.get(
            "period_start"
        )

        if raw_period_start is None:

            raise ValueError(
                "Ontbrekende period_start."
            )

        ######################################################################
        # Pyscript / Home Assistant kan hier al een datetime-object leveren.
        #
        # DAT WAS DE OORZAAK VAN DE FOUT.
        ######################################################################

        if isinstance(
            raw_period_start,
            datetime,
        ):

            start = raw_period_start

        ######################################################################
        # Eventueel kan Solcast ook een string leveren.
        ######################################################################

        elif isinstance(
            raw_period_start,
            str,
        ):

            try:

                start = datetime.fromisoformat(
                    raw_period_start
                )

            except ValueError as err:

                raise ValueError(
                    "Ongeldige period_start: "
                    f"{raw_period_start}"
                ) from err

        ######################################################################
        # Onbekend type
        ######################################################################

        else:

            raise ValueError(
                "Ongeldige period_start: "
                f"{raw_period_start}"
            )

        ######################################################################
        # Timezone controleren
        ######################################################################

        if start.tzinfo is None:

            raise ValueError(
                "period_start is niet "
                "timezone-aware: "
                f"{start}"
            )

        ######################################################################
        # Eindtijd
        #
        # detailedForecast is half-hourly; detailedHourly is hourly.
        # Solcast exposes these detailed values as average power (kW).
        ######################################################################

        if interval_minutes <= 0:

            raise ValueError(
                "Ongeldige Solcast intervalduur."
            )

        end = (
            start
            + timedelta(minutes=interval_minutes)
        )

        ######################################################################
        # PV waarden
        #
        # Convert average power (kW) to energy (kWh) for the interval.
        ######################################################################

        try:

            pv_estimate = float(
                item.get(
                    "pv_estimate",
                    0.0,
                )
            )

            pv_estimate10 = float(
                item.get(
                    "pv_estimate10",
                    0.0,
                )
            )

            pv_estimate90 = float(
                item.get(
                    "pv_estimate90",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ) as err:

            raise ValueError(
                "Ongeldige Solcast PV waarde."
            ) from err

        ######################################################################
        # Negatieve waarden voorkomen
        ######################################################################

        if pv_estimate < 0:

            pv_estimate = 0.0

        if pv_estimate10 < 0:

            pv_estimate10 = 0.0

        if pv_estimate90 < 0:

            pv_estimate90 = 0.0

        interval_hours = interval_minutes / 60.0

        pv_estimate *= interval_hours
        pv_estimate10 *= interval_hours
        pv_estimate90 *= interval_hours

        ######################################################################
        # Hour-object
        ######################################################################

        hour = Hour(

            start=start,

            end=end,

            pv_estimate=pv_estimate,

            pv_estimate10=pv_estimate10,

            pv_estimate90=pv_estimate90,

            hour_index=-1,
        )

        return hour

    ##########################################################################
    # Vandaag + morgen samenvoegen
    ##########################################################################

    def _merge(
        self,
        today: list[Hour],
        tomorrow: list[Hour],
    ) -> list[Hour]:

        hours = []

        for hour in today:

            hours.append(
                hour
            )

        for hour in tomorrow:

            hours.append(
                hour
            )

        self.logger.debug(
            f"Solcast samengevoegd: "
            f"{len(hours)} records."
        )

        return hours

    ##########################################################################
    # Dubbele uren verwijderen
    ##########################################################################

    def _remove_duplicates(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        unique = {}

        for hour in hours:

            if hour.start not in unique:

                unique[
                    hour.start
                ] = hour

        result = []

        for hour in unique.values():

            result.append(
                hour
            )

        removed = (
            len(hours)
            - len(result)
        )

        if removed:

            self.logger.debug(
                f"{removed} dubbele "
                f"Solcast uur(en) verwijderd."
            )

        return result

    ##########################################################################
    # Verlopen uren verwijderen
    ##########################################################################

    def _remove_past(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        now = (
            datetime
            .now()
            .astimezone()
        )

        result = []

        for hour in hours:

            if hour.end > now:

                result.append(
                    hour
                )

        removed = (
            len(hours)
            - len(result)
        )

        if removed:

            self.logger.debug(
                f"{removed} verlopen "
                f"Solcast uur(en) verwijderd."
            )

        return result

    ##########################################################################
    # Sorteren
    ##########################################################################

    def _sort(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        hours.sort(
            key=lambda hour: hour.start
        )

        index = 0

        for hour in hours:

            hour.hour_index = index

            index += 1

        return hours

    ##########################################################################
    # Tijdreeks controleren
    ##########################################################################

    def _validate_series(
        self,
        hours: list[Hour],
    ) -> None:

        if len(hours) < 2:

            return

        max_normal_gap = timedelta(
            hours=2
        )

        previous = hours[0]

        for index in range(
            1,
            len(hours),
        ):

            current = hours[index]

            expected_start = previous.end

            if current.start == expected_start:

                previous = current

                continue

            gap = (
                current.start
                - expected_start
            )

            if gap > max_normal_gap:

                self.logger.debug(
                    "Solcast nachtperiode tussen "
                    f"{expected_start} en "
                    f"{current.start}"
                )

                previous = current

                continue

            previous_has_pv = (
                float(previous.pv_estimate)
                > 0.0
            )

            current_has_pv = (
                float(current.pv_estimate)
                > 0.0
            )

            if (
                previous_has_pv
                and current_has_pv
            ):

                self.logger.warning(
                    "Ontbrekend Solcast uur tussen "
                    f"{expected_start} en "
                    f"{current.start}"
                )

            previous = current

    ##########################################################################
    # Statistieken
    ##########################################################################

    def _calculate_statistics(
        self,
        hours: list[Hour],
    ) -> SolcastData:

        data = SolcastData()

        data.hours = hours

        data.generated = (
            datetime
            .now()
            .astimezone()
        )

        ######################################################################
        # Geen data
        ######################################################################

        if not hours:

            self.logger.warning(
                "Geen Solcast voorspelling beschikbaar."
            )

            return data

        ######################################################################
        # Datums
        ######################################################################

        now = (
            datetime
            .now()
            .astimezone()
        )

        today = now.date()

        tomorrow = (
            today
            + timedelta(days=1)
        )

        ######################################################################
        # Totalen
        ######################################################################

        total_today = 0.0

        total_tomorrow = 0.0

        for hour in hours:

            pv = float(
                hour.pv_estimate
            )

            if hour.start.date() == today:

                total_today += pv

            elif hour.start.date() == tomorrow:

                total_tomorrow += pv

        ######################################################################
        # Resultaat
        ######################################################################

        data.total_today = (
            total_today
        )

        data.total_tomorrow = (
            total_tomorrow
        )

        data.valid_until = (
            hours[-1].end
        )

        ######################################################################
        # Logging
        ######################################################################

        self.logger.debug(
            "Solcast statistieken:"
        )

        self.logger.debug(
            f"  uren vandaag : "
            f"{total_today:.2f} kWh"
        )

        self.logger.debug(
            f"  uren morgen   : "
            f"{total_tomorrow:.2f} kWh"
        )

        self.logger.debug(
            f"  totaal        : "
            f"{total_today + total_tomorrow:.2f} kWh"
        )

        self.logger.debug(
            f"  geldig tot    : "
            f"{data.valid_until}"
        )

        return data

    ##########################################################################
    # Debug-output
    ##########################################################################

    def dump(
        self,
        data: SolcastData,
    ) -> None:

        self.logger.debug(
            ""
        )

        self.logger.debug(
            "----------- SOLCAST -----------"
        )

        for hour in data.hours:

            self.logger.debug(
                f"{hour.start:%d-%m %H:%M}"
                f"  {float(hour.pv_estimate):.3f} kWh"
                f"  P10: "
                f"{float(hour.pv_estimate10):.3f}"
                f"  P90: "
                f"{float(hour.pv_estimate90):.3f}"
            )

        self.logger.debug(
            "-------------------------------"
        )