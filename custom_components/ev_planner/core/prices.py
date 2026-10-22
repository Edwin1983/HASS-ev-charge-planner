"""
prices.py

Leest Zonneplan energieprijzen uit Home Assistant. Ondersteunt zowel
uurprijzen als kwartierprijzen.

"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import ENTITIES
from .logger import Logger
from .models import Hour, PriceData


##############################################################################
# Price reader
##############################################################################


class PriceReader:

    PRICE_FACTOR = 10_000_000

    def __init__(
        self,
        app: Any,
        logger: Logger,
        entity_id: str | None = None,
        forecast_entity_id: str | None = None,
        forecast_markup: float = 0.02,
    ) -> None:
        """
        entity_id: de sensor met de Zonneplan-uur- of kwartierprijzen.

        Als er geen entity_id wordt meegegeven, valt dit terug op de
        standaardwaarde uit core/config.py (achterwaartse compatibiliteit).
        """

        self.app = app
        self.logger = logger

        # Optionele sensor met voorspelde prijzen voor de uren waarvoor de
        # definitieve prijzen nog niet bekend zijn. Leeg = uitgeschakeld.
        self.forecast_entity_id = forecast_entity_id or None

        # Opslag in EUR/kWh op voorspelde prijzen (niet op definitieve).
        self.forecast_markup = float(forecast_markup)

        self.entity_id = (
            entity_id
            if entity_id is not None
            else ENTITIES["prices"]
        )

    ##########################################################################
    # Publieke API
    ##########################################################################

    def read(self) -> PriceData:

        forecast = self._read_forecast()

        hours = self._parse_forecast(
            forecast
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
            f"{len(data.hours)} uurprijzen geladen"
        )

        return data

    ##########################################################################
    # Voorspelde prijzen (aanvulling)
    ##########################################################################

    FORECAST_ATTRIBUTES = ("raw_today", "raw_tomorrow", "raw_forecast", "forecast")

    def read_with_forecast(self) -> tuple[PriceData, datetime | None]:
        """
        Definitieve prijzen aangevuld met voorspelde prijzen.

        Geeft (prijsdata, einde van de definitieve prijzen). Als de
        definitieve prijzen niet gelezen kunnen worden (storing, sensor
        zonder data) en er een voorspelsensor is ingesteld, dan wordt het
        plan volledig op voorspelde prijzen gebaseerd. Het einde van de
        definitieve prijzen is dan 'nu', zodat het plan automatisch wordt
        ververst zodra de echte prijzen terug zijn.
        """

        try:
            data = self.read()
            if not data.hours:
                raise RuntimeError("Geen uurprijzen beschikbaar.")
        except Exception as err:
            if not self.forecast_entity_id:
                raise

            self.logger.warning(
                "Definitieve prijzen niet beschikbaar "
                f"({type(err).__name__}: {err}); "
                "alleen voorspelde prijzen worden gebruikt."
            )

            try:
                extra = self._read_forecast_hours(None)
            except Exception:
                extra = []

            if not extra:
                raise err

            now = datetime.now().astimezone()

            for index, hour in enumerate(extra):
                hour.hour_index = index

            data = self._calculate_statistics(extra)

            # De huidige prijs komt uit de voorspelling als de eigen
            # sensor niets bruikbaars geeft.
            current = next(
                (h for h in extra if h.start <= now < h.end),
                extra[0],
            )
            data.current_price = current.price
            data.valid_until = now
            data.forecast_hours = len(extra)
            data.forecast_until = extra[-1].end

            return data, now

        final_until = data.valid_until

        return self.extend_with_forecast(data), final_until

    def extend_with_forecast(
        self,
        data: PriceData,
    ) -> PriceData:
        """
        Vul de definitieve prijzen aan met voorspelde prijzen.

        Alleen slots die starten op of na het einde van de definitieve
        prijzen worden toegevoegd, en alleen als ze daar direct op
        aansluiten. Definitieve prijzen worden dus nooit overschreven.
        Elke fout hier laat de definitieve prijzen ongemoeid.
        """

        if not self.forecast_entity_id or not data.hours:
            return data

        try:
            extra = self._read_forecast_hours(data.valid_until)
        except Exception as err:
            self.logger.warning(
                "Voorspelde prijzen konden niet worden gelezen "
                f"({type(err).__name__}: {err})"
            )
            return data

        if not extra:
            return data

        last_index = data.hours[-1].hour_index

        for offset, hour in enumerate(extra, start=1):
            hour.hour_index = last_index + offset

        data.hours = data.hours + extra
        data.forecast_hours = len(extra)
        data.forecast_until = extra[-1].end

        self.logger.debug(
            f"{len(extra)} voorspelde prijsslots toegevoegd "
            f"tot {data.forecast_until}"
        )

        return data

    def _read_forecast_hours(
        self,
        valid_until: datetime | None,
    ) -> list[Hour]:

        attributes = self.app.get_attributes(
            self.forecast_entity_id
        )

        if not isinstance(attributes, dict):
            return []

        # Alle beschikbare lijsten samenvoegen (vandaag, morgen, voorspelling).
        raw: list[Any] = []

        for name in self.FORECAST_ATTRIBUTES:
            value = attributes.get(name)
            if isinstance(value, list):
                raw.extend(value)

        if not raw:
            return []

        now = datetime.now().astimezone()

        parsed: list[Hour] = []

        for item in raw:
            try:
                hour = self._create_forecast_hour(item)
            except Exception:
                continue

            if hour.end <= now:
                continue

            if valid_until is not None and hour.start < valid_until:
                continue

            parsed.append(hour)

        parsed.sort(key=lambda hour: hour.start)

        # Dubbele slots (zelfde start in meerdere lijsten) maar één keer.
        unique: dict[datetime, Hour] = {}
        for hour in parsed:
            unique.setdefault(hour.start, hour)
        parsed = list(unique.values())

        # Alleen de reeks die direct aansluit op de definitieve prijzen.
        result: list[Hour] = []
        expected = valid_until

        for hour in parsed:
            if expected is not None and hour.start != expected:
                if not result:
                    self.logger.warning(
                        "Voorspelde prijzen sluiten niet aan op de "
                        f"definitieve prijzen ({expected} <> {hour.start})"
                    )
                break

            result.append(hour)
            expected = hour.end

        return result

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:

        if isinstance(value, datetime):
            result = value

        elif isinstance(value, str):
            result = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

        else:
            raise TypeError("ongeldig datumtype")

        if result.tzinfo is None:
            raise ValueError("datum is niet timezone-aware")

        return result

    def _create_forecast_hour(
        self,
        item: Any,
    ) -> Hour:
        """Record {start, end, value}; value in EUR/kWh, plus de opslag."""

        if not isinstance(item, dict):
            raise TypeError("geen dictionary")

        start = self._parse_datetime(item.get("start"))
        end = self._parse_datetime(item.get("end"))

        if end <= start:
            raise ValueError("einde voor start")

        raw_value = item.get("value")

        if raw_value is None or isinstance(raw_value, bool):
            raise ValueError("geen waarde")

        price = float(raw_value)

        # NaN/inf zijn nooit een geldige prijs.
        if price != price or price in (float("inf"), float("-inf")):
            raise ValueError("ongeldige prijs")

        price += self.forecast_markup

        return Hour(
            start=start,
            end=end,
            price=price,
            price_raw=price * self.PRICE_FACTOR,
            tariff_group="forecast",
            hour_index=-1,
            is_forecast=True,
        )

    ##########################################################################
    # Forecast uitlezen
    ##########################################################################

    def _read_forecast(
        self,
    ) -> list[dict[str, Any]]:

        entity_id = self.entity_id

        attributes = self.app.get_attributes(
            entity_id
        )

        if not isinstance(
            attributes,
            dict,
        ):

            raise RuntimeError(
                "Zonneplan attributes zijn geen dictionary."
            )

        forecast = attributes.get(
            "forecast"
        )

        if forecast is None:

            raise RuntimeError(
                "Forecast attribuut ontbreekt."
            )

        if not isinstance(
            forecast,
            list,
        ):


            raise RuntimeError(
                "Forecast is geen lijst."
            )

        if not forecast:


            raise RuntimeError(
                "Forecast bevat geen prijzen."
            )


        ######################################################################
        # Eerste record controleren
        # ######################################################################

        # try:

        #     first_item = forecast[0]

        #     # self.logger.warning(
        #     #     "PRICE DEBUG 45 - eerste forecast record opgehaald"
        #     # )

        #     # self.logger.warning(
        #     #     f"PRICE DEBUG 46 - eerste record type: "
        #     #     f"{type(first_item).__name__}"
        #     # )

        #     if isinstance(
        #         first_item,
        #         dict,
        #     ):
        #         pass

        #         # self.logger.warning(
        #         #     "PRICE DEBUG 47 - eerste record is dictionary"
        #         # )

        #         # self.logger.warning(
        #         #     f"PRICE DEBUG 48 - eerste record keys: "
        #         #     f"{list(first_item.keys())}"
        #         # )

        #         # self.logger.warning(
        #         #     f"PRICE DEBUG 49 - eerste start_date type: "
        #         #     f"{type(first_item.get('start_date')).__name__}"
        #         # )

        #         # self.logger.warning(
        #         #     f"PRICE DEBUG 50 - eerste electricity_price type: "
        #         #     f"{type(first_item.get('electricity_price')).__name__}"
        #         # )


        # except Exception as err:

        #     self.logger.warning(
        #         "fout bij inspectie eerste record: "
        #         f"{type(err).__name__}: {err}"
        #     )



        return forecast

    ##########################################################################
    # Huidige prijs
    ##########################################################################

    def _current_price(
        self,
    ) -> float:


        entity_id = self.entity_id

        value = self.app.get_state(
            entity_id
        )


        try:

            price = float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            self.logger.warning(
                "Ongeldige huidige prijs ontvangen."
            )

            return 0.0

        # Negatieve prijzen komen voor (overschot aan zon/wind) en zijn
        # geldig: ze horen juist bij de beste laadmomenten.

        return price

    ##########################################################################
    # Forecast parser
    ##########################################################################

    def _parse_forecast(
        self,
        forecast: list[dict[str, Any]],
    ) -> list[Hour]:


        hours = []

        index = 0

        for item in forecast:

            try:

                hour = self._create_hour(
                    item
                )


                hours.append(
                    hour
                )

            except Exception as err:

                error_type = type(
                    err
                ).__name__

                self.logger.warning(
                    f"Prijsrecord {index} "
                    f"overgeslagen "
                    f"(fouttype: {error_type}: {err})"
                )

                if isinstance(
                    item,
                    dict,
                ):

                    fields = []

                    for key in item.keys():

                        fields.append(
                            str(key)
                        )

                    self.logger.warning(
                        f"Beschikbare velden: "
                        f"{fields}"
                    )

                else:

                    self.logger.warning(
                        "Record type: "
                        f"{type(item).__name__}"
                    )

            index += 1


        return hours

    ##########################################################################
    # Eén Hour-object maken
    ##########################################################################

    def _create_hour(
        self,
        item: dict[str, Any],
    ) -> Hour:


        if not isinstance(
            item,
            dict,
        ):

            raise TypeError(
                "Prijsrecord is geen dictionary."
            )

        raw_date = item.get(
            "start_date"
        )

        if raw_date is None:

            raise ValueError(
                "Ontbrekende start_date."
            )

        if isinstance(
            raw_date,
            datetime,
        ):

            start = raw_date


        else:

            if not isinstance(
                raw_date,
                str,
            ):

                raise TypeError(
                    "start_date heeft geen geldig type."
                )

            try:

                start = datetime.fromisoformat(
                    raw_date
                )

            except (
                TypeError,
                ValueError,
            ):

                raise ValueError(
                    "Ongeldige start_date."
                )

        if start.tzinfo is None:

            raise ValueError(
                "start_date is niet timezone-aware."
            )

        ######################################################################
        # Zonneplan ondersteunt inmiddels kwartierprijzen.
        #
        # Nieuw formaat:
        #   start_date
        #   end_date
        #   price_tax_included.amount
        #
        # Oud formaat blijft ondersteund:
        #   start_date
        #   electricity_price
        #
        # De bedragen in beide Zonneplan-formaten zijn uitgedrukt in
        # 1/10.000.000 euro per kWh.
        ######################################################################

        raw_end_date = item.get(
            "end_date"
        )

        if raw_end_date is None:
            end = start + timedelta(hours=1)

        elif isinstance(
            raw_end_date,
            datetime,
        ):
            end = raw_end_date

        elif isinstance(
            raw_end_date,
            str,
        ):
            try:
                end = datetime.fromisoformat(
                    raw_end_date
                )
            except (
                TypeError,
                ValueError,
            ):
                raise ValueError(
                    "Ongeldige end_date."
                )

        else:
            raise TypeError(
                "end_date heeft geen geldig type."
            )

        if end.tzinfo is None:
            raise ValueError(
                "end_date is niet timezone-aware."
            )

        if end <= start:
            raise ValueError(
                "end_date moet na start_date liggen."
            )

        raw_price = item.get(
            "electricity_price"
        )

        if raw_price is None:
            price_block = item.get(
                "price_tax_included"
            )

            if isinstance(
                price_block,
                dict,
            ):
                raw_price = price_block.get(
                    "amount"
                )

        if raw_price is None:
            raise ValueError(
                "Ontbrekende electricity_price of "
                "price_tax_included.amount."
            )

        if isinstance(
            raw_price,
            bool,
        ):
            raise TypeError(
                "electricity_price is boolean."
            )

        try:
            price_raw = float(
                raw_price
            )
        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                "Ongeldige electricity_price."
            )

        # Negatieve prijzen zijn geldig en worden niet verworpen.
        price = (
            price_raw
            / self.PRICE_FACTOR
        )

        tariff_group = item.get(
            "tariff_group",
            "",
        )

        if not isinstance(
            tariff_group,
            str,
        ):

            tariff_group = ""

        sustainability_score = item.get(
            "sustainability_score",
            0,
        )

        if isinstance(
            sustainability_score,
            dict,
        ):
            sustainability_score = sustainability_score.get(
                "permille",
                0,
            )

        try:

            sustainability_score = float(
                sustainability_score
            )

        except (
            TypeError,
            ValueError,
        ):

            sustainability_score = 0.0


        hour = Hour(

            start=start,

            end=end,

            price=price,

            price_raw=price_raw,

            tariff_group=tariff_group,

            sustainability_score=(
                sustainability_score
            ),

            hour_index=-1,
        )


        return hour

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

        for key in unique:

            result.append(
                unique[key]
            )

        removed = (
            len(hours)
            - len(result)
        )

        if removed:

            self.logger.debug(
                f"{removed} dubbele "
                f"uur(en) verwijderd."
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
                f"uur(en) verwijderd."
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
    # Prijsreeks controleren
    ##########################################################################

    def _validate_series(
        self,
        hours: list[Hour],
    ) -> None:


        if len(hours) < 2:


            return

        index = 1

        while index < len(hours):

            previous = hours[
                index - 1
            ]

            current = hours[
                index
            ]

            expected_start = (
                previous.end
            )

            if current.start != expected_start:

                self.logger.warning(
                    "Ontbrekend uur tussen "
                    f"{expected_start} en "
                    f"{current.start}"
                )

            index += 1


    ##########################################################################
    # Statistieken
    ##########################################################################

    def _calculate_statistics(
        self,
        hours: list[Hour],
    ) -> PriceData:


        data = PriceData()


        data.hours = hours

        data.generated = (
            datetime
            .now()
            .astimezone()
        )

        if not hours:

            self.logger.warning(
                "Geen uurprijzen beschikbaar."
            )

            return data


        data.current_price = (
            self._current_price()
        )


        first_hour = hours[0]

        cheapest_price = (
            first_hour.price
        )

        highest_price = (
            first_hour.price
        )

        total_price = 0.0

        for hour in hours:

            price = hour.price

            total_price += price

            if price < cheapest_price:

                cheapest_price = price

            if price > highest_price:

                highest_price = price


        data.cheapest_price = (
            cheapest_price
        )

        data.highest_price = (
            highest_price
        )

        data.average_price = (
            total_price
            / len(hours)
        )

        data.valid_until = (
            hours[
                len(hours) - 1
            ].end
        )


        self.logger.debug(
            "Prijsstatistieken:"
        )

        self.logger.debug(
            f"  uren        : "
            f"{len(hours)}"
        )

        self.logger.debug(
            f"  huidig      : "
            f"{data.current_price:.4f}"
        )

        self.logger.debug(
            f"  minimum     : "
            f"{data.cheapest_price:.4f}"
        )

        self.logger.debug(
            f"  maximum     : "
            f"{data.highest_price:.4f}"
        )

        self.logger.debug(
            f"  gemiddeld   : "
            f"{data.average_price:.4f}"
        )

        self.logger.debug(
            f"  geldig tot  : "
            f"{data.valid_until}"
        )


        return data

    ##########################################################################
    # Debug-output
    ##########################################################################

    def dump(
        self,
        data: PriceData,
    ) -> None:

        self.logger.debug("")

        self.logger.debug(
            "------------- PRIJZEN -------------"
        )

        for hour in data.hours:

            self.logger.debug(
                f"{hour.start:%d-%m %H:%M}"
                f"  € {hour.price:.4f}"
                f"  ({hour.tariff_group})"
            )

        self.logger.debug(
            "----------------------------------"
        )
