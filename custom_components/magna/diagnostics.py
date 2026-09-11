"""Diagnostika integrácie Magna iPortál."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import MagnaConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MagnaConfigEntry
) -> dict[str, Any]:
    """Vráti stav integrácie bez prihlasovacích údajov.

    Diagnostika sa vkladá do GitHub issues, takže z nej ide von len toľko,
    koľko treba na ladenie: žiadne labely (obsahujú adresu) a z EIC kódu len
    posledné štyri znaky, nech sa dajú miesta od seba odlíšiť.
    """
    coordinator = entry.runtime_data
    data = coordinator.data

    miesta = [
        {
            "eic": p.eic,
            "kind": p.kind,
            "code": f"...{p.code[-4:]}",
            "label_length": len(p.label),
        }
        for p in (data.points if data else [])
    ]

    mesiace: dict[str, Any] = {}
    for kind, podla_mesiaca in (data.months if data else {}).items():
        mesiace[kind] = {
            mesiac.isoformat(): {
                "total": m.total,
                "cost": m.cost,
                "bands": m.bands,
                "dni_v_radoch": {n: len(v) for n, v in m.daily.items()},
            }
            for mesiac, m in sorted(podla_mesiaca.items())
        }

    zmena = data.net_change() if data else None

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "points": miesta,
        "months": mesiace,
        "net_change": (
            {"mesiac": zmena[0].isoformat(), "kwh": zmena[1]} if zmena else None
        ),
    }
