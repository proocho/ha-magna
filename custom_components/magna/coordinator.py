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

from .api import MagnaApi, MagnaAuthError, MagnaError, kind_for_label, point_code, slug
from .const import (
    CONF_ANCHOR_KWH,
    CONF_ANCHOR_MONTH,
    DOMAIN,
    GRANULARITY_DAY,
    HISTORY_MONTHS_FIRST_RUN,
    HISTORY_MONTHS_REFRESH,
    INTERVAL_MONTH,
    KIND_BANK_RETURN,
    KIND_CONSUMPTION,
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
    """Odberné miesto.

    `eic` je atribút `data-value` z portálu -- posiela sa v požiadavke, ale je
    to len poradie v rozbaľovačke a pri zmene účtu sa posunie. Na identitu
    (unique_id, statistic_id) slúži `code`, čo je EIC kód z labelu.
    """

    label: str
    kind: str
    eic: str
    code: str


@dataclass
class MagnaMonth:
    """Jeden mesiac jedného odberného miesta."""

    month: date
    total: float | None = None
    cost: float | None = None
    cost_label: str | None = None
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
    anchor_kwh: float | None = None
    anchor_month: date | None = None

    @property
    def kinds(self) -> set[str]:
        """Druhy, ktoré na účte skutočne existujú.

        Väčšina zákazníkov Magny požičovňu nemá, takže senzory pre ňu nemá
        zmysel vytvárať -- inak by natrvalo viseli na `unknown`.
        """
        return {p.kind for p in self.points}

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

    def balance(self) -> float | None:
        """Zostatok požičovne = ukotvenie + vklady − výbery po ukotvení.

        Nedrží sa tu žiadny stav: pri každom refreshi sa celý rad prepočíta
        z toho, čo povie portál. Ukotvenie je jediné číslo zvonku, lebo saldo
        portál nezverejňuje -- vie sa z neho vyčítať len mesačný tok.

        Počítajú sa len mesiace, kde majú dáta OBA toky. Bežiaci mesiac má
        vklady a nulové výbery (tie pribudnú až pri fakturácii), takže by
        zostatok umelo nafúkol.
        """
        if self.anchor_kwh is None or self.anchor_month is None:
            return None
        vklady = self.months.get(KIND_SURPLUS) or {}
        vybery = self.months.get(KIND_BANK_RETURN) or {}
        zostatok = self.anchor_kwh
        for m in sorted(vklady):
            if m <= self.anchor_month or m not in vybery:
                continue
            if not (vklady[m].has_data and vybery[m].has_data):
                continue
            zostatok += (vklady[m].total or 0.0) - (vybery[m].total or 0.0)
        return round(zostatok, 2)

    def balance_months(self) -> list[date]:
        """Mesiace, ktoré do zostatku vstúpili -- kvôli atribútu senzora."""
        if self.anchor_month is None:
            return []
        vklady = self.months.get(KIND_SURPLUS) or {}
        vybery = self.months.get(KIND_BANK_RETURN) or {}
        return [
            m
            for m in sorted(vklady)
            if m > self.anchor_month
            and m in vybery
            and vklady[m].has_data
            and vybery[m].has_data
        ]

    def net_change(self) -> tuple[date, float] | None:
        """Zmena požičovne za posledný mesiac, kde majú dáta OBA toky.

        Stačiť vklady nemôže: výbery z požičovne pribúdajú až pri fakturácii,
        takže bežiaci mesiac má vklady a nulové výbery -- a zmena by vyšla ako
        celý prebytok, čo je nezmysel.
        """
        vklady = self.months.get(KIND_SURPLUS) or {}
        vybery = self.months.get(KIND_BANK_RETURN) or {}
        spolocne = [
            m
            for m in vklady
            if m in vybery and vklady[m].has_data and vybery[m].has_data
        ]
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

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: MagnaApi,
        code: str,
        label: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {code}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
            config_entry=entry,
        )
        self.api = api
        self.point_code = code
        self.point_label = label
        self._points: list[MagnaPoint] | None = None
        self._first_run = True
        # Stiahnute mesiace sa drzia medzi refreshmi. Bezny refresh tiahne len
        # posledne dva (starsie sa uz nemenia, okrem doplnenia pozicovne pri
        # fakturacii), ale zostatok potrebuje cely rad od ukotvenia.
        self._months: dict[str, dict[date, MagnaMonth]] = {}

    async def _async_points(self) -> list[MagnaPoint]:
        """Zvolené odberné miesto + sprievodné rady účtu.

        Požičovňa a prebytok výroby nie sú samostatné odberné miesta, ale rady
        viazané na účet. Berieme ich teda vždy, ak existujú; zo skutočných
        odberných miest berieme len to, ktoré si užívateľ vybral -- inak by sa
        pri viacerých miestach navzájom prepisovali.
        """
        if self._points is not None:
            return self._points
        vsetky = [
            MagnaPoint(
                label=label, kind=kind_for_label(label), eic=eic, code=point_code(label)
            )
            for eic, label in await self.api.async_points()
        ]
        vybrane = [
            p
            for p in vsetky
            if p.kind != KIND_CONSUMPTION or p.code == self.point_code
        ]
        if not any(p.kind == KIND_CONSUMPTION for p in vybrane):
            raise MagnaError(
                f"odberné miesto {self.point_label} sa na účte už nenachádza"
            )
        self._points = vybrane
        for p in vybrane:
            _LOGGER.debug("miesto eic=%s kind=%s kód=%s", p.eic, p.kind, p.code)
        return vybrane

    def _anchor(self) -> tuple[float | None, date | None]:
        """Ukotvenie z options; prazdne, kym ho uzivatel nezada."""
        options = (self.config_entry.options if self.config_entry else None) or {}
        kwh = options.get(CONF_ANCHOR_KWH)
        raw = options.get(CONF_ANCHOR_MONTH)
        if kwh is None or not raw:
            return None, None
        try:
            mesiac = datetime.fromisoformat(str(raw)).date().replace(day=1)
        except ValueError:
            _LOGGER.warning("neplatný mesiac ukotvenia: %s", raw)
            return None, None
        return float(kwh), mesiac

    async def _async_update_data(self) -> MagnaData:
        kotva_kwh, kotva_mesiac = self._anchor()
        try:
            data = MagnaData(
                points=await self._async_points(),
                anchor_kwh=kotva_kwh,
                anchor_month=kotva_mesiac,
            )
            dnes = dt_util.now().date()
            pocet = HISTORY_MONTHS_REFRESH
            if self._first_run:
                pocet = HISTORY_MONTHS_FIRST_RUN
                if kotva_mesiac is not None:
                    # Zostatok sa sklada od ukotvenia, takze na prvom behu
                    # treba dotiahnut az po ten mesiac, aj keby bol starsi.
                    od_kotvy = (dnes.year - kotva_mesiac.year) * 12 + (
                        dnes.month - kotva_mesiac.month
                    )
                    pocet = max(pocet, od_kotvy + 1)
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
                            "mesiac %s pre %s sa nestiahol: %s", mesiac, point.kind, err
                        )
                        continue
                    data.months[point.kind][mesiac] = self._parse_month(mesiac, raw)
        except MagnaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MagnaError as err:
            raise UpdateFailed(str(err)) from err

        # Zlucit s tym, co uz mame -- refresh tiahne len posledne mesiace.
        for kind, mesiace_kind in data.months.items():
            self._months.setdefault(kind, {}).update(mesiace_kind)
        data.months = {k: dict(v) for k, v in self._months.items()}

        self._first_run = False
        self._import_statistics(data)
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
            cost_label=(summary.get("labels") or {}).get("cost"),
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

    def _import_statistics(self, data: MagnaData) -> None:
        """Zapíše denné rady ako external statistics.

        Rady sa musia najprv poskladať cez VSETKY stiahnuté mesiace a až potom
        importovať -- `_build_statistics` počíta kumulatívny súčet od nuly,
        takže import po jednotlivých mesiacoch by ho zakaždým reštartoval
        a súčty by boli nezmysel.

        Zámerne sa tu nepočíta zostatok požičovne -- recorder si kumulatívny
        súčet vedie sám, takže saldo je potom len rozdiel dvoch súčtov plus
        ukotvenie z faktúry. Menej vlastnej logiky, ktorá sa môže rozísť.
        """
        for point in data.points:
            mesiace = data.months.get(point.kind) or {}
            celkom: dict[date, float] = {}
            po_pasmach: dict[str, dict[date, float]] = {}
            for mesiac in sorted(mesiace):
                for nazov, po_dnoch in mesiace[mesiac].daily.items():
                    ciel = po_pasmach.setdefault(nazov, {})
                    for den, value in po_dnoch.items():
                        ciel[den] = ciel.get(den, 0.0) + value
                        celkom[den] = celkom.get(den, 0.0) + value

            nazov_miesta = KIND_NAMES.get(point.kind, point.kind)
            if celkom:
                self._add(
                    STAT_ID_TEMPLATE.format(code=point.code),
                    f"Magna {nazov_miesta}",
                    celkom,
                )
            # Pri jedinej serii je pasmova statistika kopia celkovej a jej nazov
            # je aj tak len nahradny ("seria 1") -- prebytok vyroby portal po
            # pasmach nerozpisuje.
            if len(po_pasmach) > 1:
                for nazov, po_dnoch in po_pasmach.items():
                    self._add(
                        STAT_ID_BAND_TEMPLATE.format(code=point.code, band=slug(nazov)),
                        f"Magna {nazov_miesta} – {nazov}",
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
