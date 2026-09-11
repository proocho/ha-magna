"""Senzory integrácie Magna iPortál."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MagnaConfigEntry
from .const import (
    DOMAIN,
    KIND_BANK_RETURN,
    KIND_CONSUMPTION,
    KIND_SURPLUS,
)
from .coordinator import MagnaCoordinator, MagnaData


@dataclass(frozen=True, kw_only=True)
class MagnaSensorDescription(SensorEntityDescription):
    """Popis senzora s funkciou, ktorá z dát vytiahne hodnotu."""

    value: Callable[[MagnaData], float | None]
    extra: Callable[[MagnaData], dict[str, str | float]] | None = None
    # Senzor sa vytvorí, len ak účet tieto druhy odberných miest naozaj má.
    # Väčšina zákazníkov Magny požičovňu nemá a tri natrvalo prázdne senzory
    # sú horšie než žiadne.
    requires: frozenset[str] = frozenset()


def _mesiac_atributy(data: MagnaData, kind: str) -> dict[str, str | float]:
    """Za ktorý mesiac hodnota platí a ako je rozložená po pásmach.

    Mesiac je tu zámerne -- priebehy požičovne pribúdajú až pri fakturácii,
    takže hodnota nemusí byť za aktuálny mesiac a bez tohto atribútu by to
    nebolo ako zistiť.
    """
    m = data.newest(kind)
    if m is None:
        return {}
    out: dict[str, str | float] = {"mesiac": m.month.strftime("%m/%Y")}
    for nazov, value in sorted(m.bands.items()):
        out[nazov] = round(value, 2)
    return out


def _total(kind: str) -> Callable[[MagnaData], float | None]:
    def _f(data: MagnaData) -> float | None:
        m = data.newest(kind)
        return None if m is None or m.total is None else round(m.total, 2)

    return _f


def _cost(data: MagnaData) -> float | None:
    m = data.newest(KIND_CONSUMPTION)
    return None if m is None or m.cost is None else round(m.cost, 2)


def _cost_extra(data: MagnaData) -> dict[str, str | float]:
    """K cene patrí doslovný popis z portálu.

    Portál počíta „Celkové náklady v 4T" každému, aj tomu, kto štvortarif
    nemá -- vtedy je to hypotetické číslo, koľko by platil, keby prešiel.
    Preto sa doslovný popis zobrazuje ako atribút, nech je jasné, čo to je.
    """
    out = _mesiac_atributy(data, KIND_CONSUMPTION)
    m = data.newest(KIND_CONSUMPTION)
    if m is not None and m.cost_label:
        out["popis"] = m.cost_label
    return out


def _net(data: MagnaData) -> float | None:
    zmena = data.net_change()
    return None if zmena is None else zmena[1]


def _net_extra(data: MagnaData) -> dict[str, str | float]:
    zmena = data.net_change()
    if zmena is None:
        return {}
    mesiac, hodnota = zmena
    vklady = (data.months.get(KIND_SURPLUS) or {}).get(mesiac)
    vybery = (data.months.get(KIND_BANK_RETURN) or {}).get(mesiac)
    return {
        "mesiac": mesiac.strftime("%m/%Y"),
        "vklady": round(vklady.total or 0.0, 2) if vklady else 0.0,
        "vybery": round(vybery.total or 0.0, 2) if vybery else 0.0,
    }


SENSORS: tuple[MagnaSensorDescription, ...] = (
    MagnaSensorDescription(
        key="spotreba_mesiac",
        translation_key="spotreba_mesiac",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value=_total(KIND_CONSUMPTION),
        extra=lambda d: _mesiac_atributy(d, KIND_CONSUMPTION),
    ),
    MagnaSensorDescription(
        key="naklady_mesiac",
        translation_key="naklady_mesiac",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value=_cost,
        extra=_cost_extra,
    ),
    MagnaSensorDescription(
        key="prebytok_mesiac",
        translation_key="prebytok_mesiac",
        requires=frozenset({KIND_SURPLUS}),
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value=_total(KIND_SURPLUS),
        extra=lambda d: _mesiac_atributy(d, KIND_SURPLUS),
    ),
    MagnaSensorDescription(
        key="pozicovna_mesiac",
        translation_key="pozicovna_mesiac",
        requires=frozenset({KIND_BANK_RETURN}),
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value=_total(KIND_BANK_RETURN),
        extra=lambda d: _mesiac_atributy(d, KIND_BANK_RETURN),
    ),
    MagnaSensorDescription(
        key="banka_zmena",
        translation_key="banka_zmena",
        requires=frozenset({KIND_SURPLUS, KIND_BANK_RETURN}),
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value=_net,
        extra=_net_extra,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MagnaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Vytvorí senzory."""
    coordinator = entry.runtime_data
    dostupne = coordinator.data.kinds
    async_add_entities(
        MagnaSensor(coordinator, entry, description)
        for description in SENSORS
        if description.requires <= dostupne
    )


class MagnaSensor(CoordinatorEntity[MagnaCoordinator], SensorEntity):
    """Senzor nad mesačnými dátami z iPortálu."""

    _attr_has_entity_name = True
    entity_description: MagnaSensorDescription

    def __init__(
        self,
        coordinator: MagnaCoordinator,
        entry: MagnaConfigEntry,
        description: MagnaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # Podla odberneho miesta, nie "Magna iPortal" - na jednom ucte
            # moze byt viac miest a kazde je vlastny config entry.
            name=entry.title or "Magna iPortál",
            manufacturer="MAGNA ENERGIA",
            configuration_url="https://iportal.magna-energia.sk/",
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str | float] | None:
        if self.entity_description.extra is None:
            return None
        return self.entity_description.extra(self.coordinator.data) or None
