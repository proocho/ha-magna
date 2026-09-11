"""Klient pre Magna iPortál (iportal.magna-energia.sk).

Portál nemá dokumentované API. Toto je odvodené z jeho vlastného
`js/scripts.min.js` (funkcia `mg_get_data()`) a z odchytenej komunikácie,
overené 10.–11. 9. 2026. Podrobný prieskum je v misc/magna/POZNAMKY.md.

Endpointy (všetko form-encoded POST, session drží PHPSESSID):
    GET  /                 -> HTML + PHPSESSID
    POST /ajax/login.php   login=..&heslo=..  alebo  demo=true
    POST /ajax/logout.php
    POST /ajax/load.php    chartType, date, interval, option, typ, eic,
                           poradie[], force, granularity
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import (
    BASE_URL,
    GRANULARITY_DAY,
    INTERVAL_MONTH,
    KIND_BANK_RETURN,
    KIND_CONSUMPTION,
    SUFFIX_TO_KIND,
    TYP_4T,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
}

_TR_RE = re.compile(r"<tr([^>]*)>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_NUM_RE = re.compile(r"^([\d\s.,]+)\s*(kWh|EUR)$")
# Odberne miesta su <div class='option' data-value='N'>Label</div> vnutri
# <div class='options'> v bloku custom_select miesto. POZOR: povodna verzia
# hladala <ul>, co na stranke naslo az legendu pasiem (#sortable_standard)
# a vratila "Spotreba VT" / "Spotreba NT" namiesto odbernych miest.
_SELECT_RE = re.compile(
    r"custom_select\s+miesto.*?<div\s+class='options'>(.*?)</div>\s*</div>", re.S | re.I
)
_OPTION_RE = re.compile(
    r"<div[^>]*class='option[^']*'[^>]*data-value='([^']*)'[^>]*>(.*?)</div>", re.S | re.I
)


class MagnaError(Exception):
    """Chyba komunikácie s portálom."""


class MagnaAuthError(MagnaError):
    """Neúspešné prihlásenie -- vyvolá reauth flow."""


def _text(raw: str) -> str:
    return html_mod.unescape(_TAG_RE.sub("", raw)).strip()


def _number(raw: str) -> tuple[float, str] | None:
    m = _NUM_RE.match(raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")), m.group(2)
    except ValueError:
        return None


def slug(name: str) -> str:
    """Názov pásma -> kľúč použiteľný v statistic_id."""
    prevod = str.maketrans("áäčďéíĺľňóôöŕšťúüýž", "aacdeillnooorstuuyz")
    s = name.lower().translate(prevod)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "nezname"


def kind_for_label(label: str) -> str:
    """Druh odberného miesta podľa sufixu labelu."""
    low = re.sub(r"\s+", " ", label).strip().lower()
    for suffix, kind in SUFFIX_TO_KIND.items():
        if low.endswith(suffix):
            return kind
    return KIND_CONSUMPTION


def typ_for_label(label: str) -> int:
    """Tarifný pohľad. Vždy 4T -- inak by prišla len štandardná tarifa."""
    return TYP_4T


def parse_summary(text_sumar: str | None) -> dict[str, Any]:
    """Rozparsuje HTML tabuľku so súhrnom.

    Portál si súčty počíta sám, takže sa nescitávajú série -- tým sa aj
    vyhneme zaokrúhľovacím rozdielom proti tomu, čo vidí užívateľ.

    Popisy „dôležitých" riadkov sa medzi odbernými miestami líšia (spotreba
    vs. „Celkový prebytok za mesiac" vs. „Celková vrátená elektrina"), preto
    sa rozlišuje podľa jednotky, nie podľa textu.
    """
    out: dict[str, Any] = {"total": None, "cost": None, "bands": {}, "labels": {}}
    if not text_sumar:
        return out
    for attrs, body in _TR_RE.findall(text_sumar):
        tds = _TD_RE.findall(body)
        if len(tds) < 2:
            continue
        popis = _text(tds[0])
        cislo = _number(_text(tds[1]))
        if not popis or cislo is None:
            continue
        hodnota, jednotka = cislo
        dolezity = "important" in attrs
        if dolezity:
            if jednotka == "EUR" and out["cost"] is None:
                out["cost"] = hodnota
                out["labels"]["cost"] = popis
            elif jednotka == "kWh" and out["total"] is None:
                out["total"] = hodnota
                out["labels"]["total"] = popis
        elif jednotka == "kWh":
            out["bands"][popis] = hodnota
    return out


def parse_series(payload: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """`data.data_sets[i].data` = [[index, kWh], ...].

    Pozor: `data_sets` sú vnorené v `data`, nie na najvyššej úrovni.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    out: list[list[tuple[float, float]]] = []
    for ds in data.get("data_sets") or []:
        body: list[tuple[float, float]] = []
        for point in ds.get("data") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                x, y = point[0], point[1]
                if y is None:
                    continue
                try:
                    body.append((float(x), float(y)))
                except (TypeError, ValueError):
                    continue
        out.append(body)
    return out


def pair_series(
    payload: dict[str, Any], summary: dict[str, Any]
) -> dict[str, list[tuple[float, float]]]:
    """Priradí sériám názvy pásiem.

    POZOR: poradie `data_sets` NEZODPOVEDÁ poradiu riadkov v súhrne -- overené
    v demo režime, kde prvá séria sedela na druhý riadok tabuľky. Preto sa
    použije `series_names`, ak ho portál pošle, a inak sa páruje podľa toho,
    ktorému súčtu sa séria najviac podobá.
    """
    series = parse_series(payload)
    if not series:
        return {}
    names = payload.get("series_names")
    if isinstance(names, list) and len(names) == len(series):
        return {str(n): s for n, s in zip(names, series) if n}

    bands = dict(summary.get("bands") or {})
    out: dict[str, list[tuple[float, float]]] = {}
    for s in series:
        total = round(sum(y for _, y in s), 2)
        najblizsi = None
        najlepsi = None
        for name, value in bands.items():
            rozdiel = abs(value - total)
            if najlepsi is None or rozdiel < najlepsi:
                najlepsi, najblizsi = rozdiel, name
        # tolerancia 0,5 kWh -- zaokrúhľovanie v portáli
        if najblizsi is not None and najlepsi is not None and najlepsi <= 0.5:
            out[najblizsi] = s
            bands.pop(najblizsi)
        else:
            out.setdefault(f"séria {len(out) + 1}", s)
    return out


class MagnaApi:
    """Minimálny klient portálu."""

    def __init__(
        self,
        session: ClientSession,
        username: str | None = None,
        password: str | None = None,
        demo: bool = False,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._demo = demo
        self._logged_in = False

    async def _request(
        self, path: str, data: dict[str, Any] | list[tuple[str, str]] | None = None
    ) -> str:
        url = f"{BASE_URL}/{path.lstrip('/')}"
        headers = dict(_HEADERS)
        if data is not None:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Accept"] = "*/*"
            headers["Referer"] = f"{BASE_URL}/spotreba"
        try:
            async with self._session.request(
                "POST" if data is not None else "GET", url, data=data, headers=headers
            ) as resp:
                text = await resp.text()
                if resp.status == 466:
                    raise MagnaError(
                        "portál odmietol požiadavku (HTTP 466) -- chýbajúce hlavičky"
                    )
                if resp.status >= 400:
                    raise MagnaError(f"HTTP {resp.status} na {path}")
                return text
        except ClientError as err:
            raise MagnaError(f"spojenie s portálom zlyhalo: {err}") from err

    async def _json(self, path: str, data: Any) -> dict[str, Any]:
        text = await self._request(path, data)
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise MagnaError(f"{path} nevrátil JSON: {text[:150]}") from err
        if not isinstance(payload, dict):
            raise MagnaError(f"{path} vrátil neočakávaný typ")
        if payload.get("err"):
            raise MagnaError(payload.get("text") or f"{path} ohlásil chybu")
        return payload

    async def async_login(self) -> None:
        """Naštartuje session a prihlási sa."""
        await self._request("/")
        if self._demo:
            await self._json("ajax/login.php", {"demo": "true"})
        else:
            if not self._username or not self._password:
                raise MagnaAuthError("chýba prihlasovacie meno alebo heslo")
            try:
                await self._json(
                    "ajax/login.php",
                    {"login": self._username, "heslo": self._password},
                )
            except MagnaError as err:
                raise MagnaAuthError(str(err)) from err
        self._logged_in = True

    async def async_logout(self) -> None:
        if not self._logged_in:
            return
        try:
            await self._request("ajax/logout.php", {})
        except MagnaError:
            _LOGGER.debug("odhlásenie z portálu zlyhalo, ignorujem")
        self._logged_in = False

    async def async_points(self) -> list[tuple[str, str]]:
        """Odberné miesta ako dvojice (eic, label).

        `eic` je atribút `data-value` položky, NIE jej poradie -- portál to
        číta rovnako (`$('.custom_select.miesto .selection').attr("data-value")`
        v `mg_get_data()`). Label je parameter `option`, posiela sa ako
        viditeľný text.
        """
        if not self._logged_in:
            await self.async_login()
        page = await self._request("/spotreba")
        blok = _SELECT_RE.search(page)
        if not blok:
            raise MagnaError("v portáli sa nenašiel výber odberného miesta")
        out: list[tuple[str, str]] = []
        for eic, label in _OPTION_RE.findall(blok.group(1)):
            label = re.sub(r"\s+", " ", _text(label)).strip()
            if label and not any(label == existing for _, existing in out):
                out.append((eic.strip(), label))
        if not out:
            raise MagnaError("v portáli sa nenašlo ani jedno odberné miesto")
        return out

    async def async_load(
        self,
        date: str,
        option: str,
        eic: str,
        interval: int = INTERVAL_MONTH,
        granularity: int = GRANULARITY_DAY,
    ) -> dict[str, Any]:
        """Stiahne jedno okno dát a vráti súhrn + pomenované série."""
        if not self._logged_in:
            await self.async_login()
        params: list[tuple[str, str]] = [
            ("chartType", "stacked"),
            ("date", date),
            ("interval", str(interval)),
            ("option", option),
            ("typ", str(typ_for_label(option))),
            ("eic", str(eic)),
        ]
        params += [("poradie[]", str(i)) for i in (1, 2, 3, 4)]
        params += [("force", "0"), ("granularity", str(granularity))]
        payload = await self._json("ajax/load.php", params)
        summary = parse_summary(payload.get("text_sumar"))
        return {
            "summary": summary,
            "series": pair_series(payload, summary),
            "params": payload.get("params") or {},
            "header": _text(payload.get("interval_header") or ""),
            "is_ims": bool(payload.get("isIms")),
        }
