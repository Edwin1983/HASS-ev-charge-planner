"""
prices.py

Leest Zonneplan energieprijzen uit Home Assistant. Ondersteunt zowel\nuurprijzen als kwartierprijzen.

Extra DEBUG/WARNING logging toegevoegd om Pyscript-problemen
gericht te lokaliseren.
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
    ) -> None:
        """
        entity_id: de sensor met de Zonneplan-uur- of kwartierprijzen.

        Als er geen entity_id wordt meegegeven, valt dit terug op de
        standaardwaarde uit core/config.py (achterwaartse compatibiliteit).
        """

        self.app = app
        self.logger = logger

        self.entity_id = (
            entity_id
            if entity_id is not None
            else ENTITIES["prices"]
        )

    ##########################################################################
    # Publieke API
    ##########################################################################

    def read(self) -> PriceData:

        # self.logger.warning(
        #     "PRICE DEBUG 01 - read() gestart"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 02 - _read_forecast() aanroepen"
        # )
        
        forecast = self._read_forecast()

        # self.logger.warning(
        #     "PRICE DEBUG 03 - _read_forecast() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 04 - forecast type: "
        #     f"{type(forecast).__name__}"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 05 - aantal forecast records: "
        #     f"{len(forecast)}"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 06 - _parse_forecast() aanroepen"
        # )
        
        hours = self._parse_forecast(
            forecast
        )

        # self.logger.warning(
        #     "PRICE DEBUG 07 - _parse_forecast() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 08 - aantal Hour-objecten: "
        #     f"{len(hours)}"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 09 - _remove_duplicates() aanroepen"
        # )
        
        hours = self._remove_duplicates(
            hours
        )

        # self.logger.warning(
        #     "PRICE DEBUG 10 - _remove_duplicates() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 11 - aantal uren na duplicates: "
        #     f"{len(hours)}"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 12 - _remove_past() aanroepen"
        # )
        
        hours = self._remove_past(
            hours
        )

        # self.logger.warning(
        #     "PRICE DEBUG 13 - _remove_past() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 14 - aantal uren na past: "
        #     f"{len(hours)}"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 15 - _sort() aanroepen"
        # )
        
        hours = self._sort(
            hours
        )

        # self.logger.warning(
        #     "PRICE DEBUG 16 - _sort() teruggekeerd"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 17 - _validate_series() aanroepen"
        # )
        
        self._validate_series(
            hours
        )

        # self.logger.warning(
        #     "PRICE DEBUG 18 - _validate_series() teruggekeerd"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 19 - _calculate_statistics() aanroepen"
        # )
        
        data = self._calculate_statistics(
            hours
        )

        # self.logger.warning(
        #     "PRICE DEBUG 20 - _calculate_statistics() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 21 - PriceData hours: "
        #     f"{len(data.hours)}"
        # )

        self.logger.debug(
            f"{len(data.hours)} uurprijzen geladen"
        )

        # self.logger.warning(
        #     "PRICE DEBUG 22 - read() succesvol afgerond"
        # )
        
        return data

    ##########################################################################
    # Forecast uitlezen
    ##########################################################################

    def _read_forecast(
        self,
    ) -> list[dict[str, Any]]:

        # self.logger.warning(
        #     "PRICE DEBUG 30 - _read_forecast() gestart"
        # )
        
        entity_id = self.entity_id

        # self.logger.warning(
        #     "PRICE DEBUG 32 - get_attributes() aanroepen"
        # )
        
        attributes = self.app.get_attributes(
            entity_id
        )

        # self.logger.warning(
        #     "PRICE DEBUG 33 - get_attributes() teruggekeerd"
        # )

        # self.logger.warning(
        #     f"PRICE DEBUG 34 - attributes type: "
        #     f"{type(attributes).__name__}"
        # )
        
        if not isinstance(
            attributes,
            dict,
        ):

            # self.logger.warning(
            #     "PRICE DEBUG 35 - attributes GEEN dictionary"
            # )
        
            raise RuntimeError(
                "Zonneplan attributes zijn geen dictionary."
            )

        # self.logger.warning(
        #     "PRICE DEBUG 36 - attributes is dictionary"
        # )

        # self.logger.warning(
        #     "PRICE DEBUG 37 - attributes.get('forecast') aanroepen"
        # )
        
        forecast = attributes.get(
            "forecast"
        )

        # self.logger.warning(
        #     "PRICE DEBUG 38 - attributes.get('forecast') teruggekeerd"
        # )
        
        # self.logger.warning(
        #     f"PRICE DEBUG 39 - forecast type: "
        #     f"{type(forecast).__name__}"
        # )

        if forecast is None:

            # self.logger.warning(
            #     "PRICE DEBUG 40 - forecast is None"
            # )
        
            raise RuntimeError(
                "Forecast attribuut ontbreekt."
            )

        if not isinstance(
            forecast,
            list,
        ):

            # self.logger.warning(
            #     "PRICE DEBUG 41 - forecast GEEN lijst"
            # )
        

            raise RuntimeError(
                "Forecast is geen lijst."
            )

        # self.logger.warning(
        #     "PRICE DEBUG 42 - forecast is lijst"
        # )
        
        if not forecast:

            # self.logger.warning(
            #     "PRICE DEBUG 43 - forecast is leeg"
            # )
        

            raise RuntimeError(
                "Forecast bevat geen prijzen."
            )

        # self.logger.warning(
        #     f"PRICE DEBUG 44 - "
        #     f"{len(forecast)} forecast records ontvangen"
        # )
        

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


        # self.logger.warning(
        #     "PRICE DEBUG 52 - _read_forecast() succesvol afgerond"
        # )
        

        return forecast

    ##########################################################################
    # Huidige prijs
    ##########################################################################

    def _current_price(
        self,
    ) -> float:

        # self.logger.warning(
        #     "PRICE DEBUG 60 - _current_price() gestart"
        # )
        

        entity_id = self.entity_id

        value = self.app.get_state(
            entity_id
        )

        # self.logger.warning(
        #     f"PRICE DEBUG 61 - huidige prijs state type: "
        #     f"{type(value).__name__}"
        # )
        

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

        if price < 0:

            self.logger.warning(
                "Huidige prijs is negatief."
            )

            return 0.0

        # self.logger.warning(
        #     "PRICE DEBUG 62 - _current_price() succesvol"
        # )
        

        return price

    ##########################################################################
    # Forecast parser
    ##########################################################################

    def _parse_forecast(
        self,
        forecast: list[dict[str, Any]],
    ) -> list[Hour]:

        # self.logger.warning(
        #     "PRICE DEBUG 70 - _parse_forecast() gestart"
        # )
        

        hours = []

        index = 0

        for item in forecast:

            # self.logger.warning(
            #     f"PRICE DEBUG 71 - record {index} verwerken"
            # )

            try:

                hour = self._create_hour(
                    item
                )

                # self.logger.warning(
                #     f"PRICE DEBUG 72 - record {index} "
                #     f"succesvol omgezet naar Hour"
                # )


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

        # self.logger.warning(
        #     f"PRICE DEBUG 73 - _parse_forecast() klaar, "
        #     f"{len(hours)} Hour-objecten gemaakt"
        # )


        return hours

    ##########################################################################
    # Eén Hour-object maken
    ##########################################################################

    def _create_hour(
        self,
        item: dict[str, Any],
    ) -> Hour:

        # self.logger.warning(
        #     "PRICE DEBUG 80 - _create_hour() gestart"
        # )


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

        # self.logger.warning(
        #     f"PRICE DEBUG 81 - start_date type: "
        #     f"{type(raw_date).__name__}"
        # )

        if raw_date is None:

            raise ValueError(
                "Ontbrekende start_date."
            )

        if isinstance(
            raw_date,
            datetime,
        ):

            start = raw_date

            # self.logger.warning(
            #     "PRICE DEBUG 82 - start_date is datetime"
            # )


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

        if price_raw < 0:
            raise ValueError(
                "electricity_price mag niet negatief zijn."
            )

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
            "sustainability_score"
        )

        if sustainability_score is None:
            score_block = item.get(
                "sustainability_score"
            )

            if isinstance(
                score_block,
                dict,
            ):
                sustainability_score = score_block.get(
                    "permille",
                    0,
                )

        if sustainability_score is None:
            sustainability_score = 0

        try:

            sustainability_score = float(
                sustainability_score
            )

        except (
            TypeError,
            ValueError,
        ):

            sustainability_score = 0.0

        # self.logger.warning(
        #     "PRICE DEBUG 84 - Hour() object aanmaken"
        # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 85 - Hour() object succesvol"
        # )


        return hour

    ##########################################################################
    # Dubbele uren verwijderen
    ##########################################################################

    def _remove_duplicates(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        # self.logger.warning(
        #     "PRICE DEBUG 90 - _remove_duplicates() gestart"
        # )


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

        # self.logger.warning(
        #     f"PRICE DEBUG 91 - _remove_duplicates() klaar: "
        #     f"{len(result)} uren"
        # )


        return result

    ##########################################################################
    # Verlopen uren verwijderen
    ##########################################################################

    def _remove_past(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        # self.logger.warning(
        #     "PRICE DEBUG 100 - _remove_past() gestart"
        # )


        now = (
            datetime
            .now()
            .astimezone()
        )

        # self.logger.warning(
        #     f"PRICE DEBUG 101 - now: {now}"
        # )
        

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

        # self.logger.warning(
        #     f"PRICE DEBUG 102 - _remove_past() klaar: "
        #     f"{len(result)} uren"
        # )


        return result

    ##########################################################################
    # Sorteren
    ##########################################################################

    def _sort(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        # self.logger.warning(
        #     "PRICE DEBUG 110 - _sort() gestart"
        # )


        hours.sort(
            key=lambda hour: hour.start
        )

        index = 0

        for hour in hours:

            hour.hour_index = index

            index += 1

        # self.logger.warning(
        #     "PRICE DEBUG 111 - _sort() klaar"
        # )


        return hours

    ##########################################################################
    # Prijsreeks controleren
    ##########################################################################

    def _validate_series(
        self,
        hours: list[Hour],
    ) -> None:

        # self.logger.warning(
        #     "PRICE DEBUG 120 - _validate_series() gestart"
        # )


        if len(hours) < 2:

            # self.logger.warning(
            #     "PRICE DEBUG 121 - minder dan 2 uren"
            # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 122 - _validate_series() klaar"
        # )


    ##########################################################################
    # Statistieken
    ##########################################################################

    def _calculate_statistics(
        self,
        hours: list[Hour],
    ) -> PriceData:

        # self.logger.warning(
        #     "PRICE DEBUG 130 - _calculate_statistics() gestart"
        # )


        data = PriceData()

        # self.logger.warning(
        #     "PRICE DEBUG 131 - PriceData() aangemaakt"
        # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 132 - uren aanwezig"
        # )


        data.current_price = (
            self._current_price()
        )

        # self.logger.warning(
        #     "PRICE DEBUG 133 - current_price berekend"
        # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 134 - prijsstatistieken berekend"
        # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 135 - PriceData gevuld"
        # )


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

        # self.logger.warning(
        #     "PRICE DEBUG 136 - _calculate_statistics() succesvol"
        # )


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
