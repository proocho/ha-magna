"""Koordinátor integrácie Magna iPortál."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import MagnaApi, MagnaAuthError, MagnaError, kind_for_label, slug
from .const import (
    DOMAIN,
    GRANULARITY_DAY,
    HISTORY_MONTHS_FIRST_RUN,
    HISTORY_MONTHS_REFRESH,
    INTERVAL_MONTH,
    KIND_BANK_RETURN,
    KIND_NAMES,
    KIND_SURPLUS,
    STAT_ID_BAND_TEMPLATE,
    STAT_ID_TEMPLATE,
    UNIT_KWH,
    UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class MagnaPoint:
    """Odberné miesto. `eic` je index v zozname, `label` je parameter option."""

    label: str
    kind: str
    eic: int


@dataclass
class MagnaMonth:
    """Jeden mesiac jedného odberného miesta."""

    month: date
    total: float | None = None
    cost: float | None = None
    bands: dict[str, float] = field(default_factory=dict)
    daily: dict[str, dict[date, float]] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(self.total)


@dataclass
class MagnaData:
    """Stav integrácie."""

    points: list[MagnaPoint] = field(default_factory=list)
    months: dict[str, dict[date, MagnaMonth]] = field(default_factory=dict)

    def newest(self, kind: str) -> MagnaMonth | None:
        """Najnovší mesiac s dátami.

        Priebehy požičovne pribúdajú až pri fakturácii, takže „najnovší
        s dátami" nie je to isté ako „tento mesiac" -- a senzor musí povedať,
        za ktorý mesiac hodnota platí.
        """
        mesiace = self.months.get(kind) or {}
        for m in sorted(mesiace, reverse=True):
            if mesiace[m].has_data:
                return mesiace[m]
        return None

    def net_change(self) -> tuple[date, float] | None:
        """Zmena banky za posledný mesiac, kde sú oba toky."""
        vklady = self.months.get(KIND_SURPLUS) or {}
        vybery = self.months.get(KIND_BANK_RETURN) or {}
        spolocne = [m for m in vklady if m in vybery and vklady[m].has_data]
        if not spolocne:
            return None
        m = max(spolocne)
        return m, round((vklady[m].total or 0.0) - (vybery[m].total or 0.0), 2)


def _first_of_month(value: date) -> date:
    return value.replace(day=1)


def _month_back(value: date, count: int) -> date:
    """`count` mesiacov dozadu od prvého dňa mesiaca."""
    y, m = value.year, value.month - count
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _build_statistics(daily: dict[date, float]) -> list[StatisticData]:
    """Kumulatívny rad; recorder prepíše body s rovnakým časom, takže import
    je idempotentný a môže sa opakovať pri každom update."""
    out: list[StatisticData] = []
    total = 0.0
    for day in sorted(daily):
        total += daily[day]
        out.append(
            StatisticData(
                start=dt_util.start_of_local_day(day),
                state=round(daily[day], 4),
                sum=round(total, 4),
            )
        )
    return out


class MagnaCoordinator(DataUpdateCoordinator[MagnaData]):
    """Sťahuje mesačné dáta a plní dlhodobé štatistiky."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MagnaApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
            config_entry=entry,
        )
        self.api = api
        self._points: list[MagnaPoint] | None = None
        self._first_run = True

    async def _async_points(self) -> list[MagnaPoint]:
        if self._points is not None:
            return self._points
        labels = await self.api.async_points()
        self._points = [
            MagnaPoint(label=label, kind=kind_for_label(label), eic=index)
            for index, label in enumerate(labels)
        ]
        for p in self._points:
            _LOGGER.debug("odberné miesto eic=%s kind=%s: %s", p.eic, p.kind, p.label)
        return self._points

    async def _async_update_data(self) -> MagnaData:
        try:
            data = MagnaData(points=await self._async_points())
            pocet = (
                HISTORY_MONTHS_FIRST_RUN if self._first_run else HISTORY_MONTHS_REFRESH
            )
            dnes = dt_util.now().date()
            mesiace = [_month_back(_first_of_month(dnes), i) for i in range(pocet)]

            for point in data.points:
                data.months.setdefault(point.kind, {})
                for mesiac in mesiace:
                    try:
                        raw = await self.api.async_load(
                            mesiac.isoformat(),
                            point.label,
                            point.eic,
                            interval=INTERVAL_MONTH,
                            granularity=GRANULARITY_DAY,
                        )
                    except MagnaError as err:
                        # Jeden chýbajúci mesiac nemá zhodiť celý update --
                        # portál pre staršie obdobia vracia prázdno.
                        _LOGGER.debug(
                            "mesiac %s pre %s sa nestiahol: %s",
                            mesiac,
                            point.kind,
                            err,
                        )
                        continue
                    data.months[point.kind][mesiac] = self._parse_month(mesiac, raw)
        except MagnaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MagnaError as err:
            raise UpdateFailed(str(err)) from err

        self._first_run = False
        await self._async_import_statistics(data)
        return data

    @staticmethod
    def _parse_month(mesiac: date, raw: dict) -> MagnaMonth:
        summary = raw["summary"]
        # Prvý deň okna berieme z params, ktoré portál vracia -- indexy v sérii
        # sú relatívne k nemu, nie k dátumu, ktorý sme poslali.
        zac = (raw.get("params") or {}).get("dateFrom")
        try:
            prvy = datetime.fromisoformat(zac).date() if zac else mesiac
        except (TypeError, ValueError):
            prvy = mesiac
        out = MagnaMonth(
            month=mesiac,
            total=summary.get("total"),
            cost=summary.get("cost"),
            bands=dict(summary.get("bands") or {}),
        )
        for nazov, series in (raw.get("series") or {}).items():
            po_dnoch: dict[date, float] = {}
            for index, value in series:
                try:
                    den = prvy + timedelta(days=int(index) - 1)
                except (TypeError, ValueError, OverflowError):
                    continue
                if den.month == prvy.month:
                    po_dnoch[den] = po_dnoch.get(den, 0.0) + value
            if po_dnoch:
                out.daily[nazov] = po_dnoch
        return out

    async def _async_import_statistics(self, data: MagnaData) -> None:
        """Zapíše denné rady ako external statistics.

        Zámerne sa tu nepočíta zostatok banky -- recorder si kumulatívny súčet
        vedie sám, takže saldo je potom len rozdiel dvoch súčtov plus ukotvenie
        z faktúry. Menej vlastnej logiky, ktorá sa môže rozísť.
        """
        for kind, mesiace in data.months.items():
            celkom: dict[date, float] = {}
            po_pasmach: dict[str, dict[date, float]] = {}
            for mesiac in sorted(mesiace):
                m = mesiace[mesiac]
                for nazov, po_dnoch in m.daily.items():
                    ciel = po_pasmach.setdefault(nazov, {})
                    for den, value in po_dnoch.items():
                        ciel[den] = ciel.get(den, 0.0) + value
                        celkom[den] = celkom.get(den, 0.0) + value

            if celkom:
                self._add(
                    STAT_ID_TEMPLATE.format(kind=kind),
                    f"Magna {KIND_NAMES.get(kind, kind)}",
                    celkom,
                )
            for nazov, po_dnoch in po_pasmach.items():
                if not po_dnoch:
                    continue
                self._add(
                    STAT_ID_BAND_TEMPLATE.format(kind=kind, band=slug(nazov)),
                    f"Magna {KIND_NAMES.get(kind, kind)} – {nazov}",
                    po_dnoch,
                )

    def _add(self, statistic_id: str, name: str, daily: dict[date, float]) -> None:
        statistics = _build_statistics(daily)
        if not statistics:
            return
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=name,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UNIT_KWH,
            unit_class=EnergyConverter.UNIT_CLASS,
        )
        async_add_external_statistics(self.hass, metadata, statistics)
