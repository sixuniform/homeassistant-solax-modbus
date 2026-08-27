"""Writable fixed-width text entities for the SolaX Modbus integration."""

import logging
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MODBUS_ADDR,
    DEFAULT_MODBUS_ADDR,
    DOMAIN,
    REGISTER_U16,
    WRITE_MULTI_MODBUS,
    BaseModbusTextEntityDescription,
    matches_active_when,
    matches_modbus_protocol,
)

_LOGGER = logging.getLogger(__name__)


def _encode_fixed_width_ascii(value: str, wordcount: int) -> list[int]:
    """Encode a NUL-terminated ASCII string into an exact register span."""
    if wordcount <= 0:
        raise ValueError("wordcount must be positive")
    capacity = wordcount * 2
    encoded = value.encode("ascii")
    if len(encoded) >= capacity:
        raise ValueError(f"value must use at most {capacity - 1} ASCII bytes")
    padded = (encoded + b"\x00").ljust(capacity, b"\x00")
    return [int.from_bytes(padded[offset : offset + 2], "big") for offset in range(0, capacity, 2)]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    """Set up writable Modbus text entities."""
    if entry.data:
        hub_name = entry.data[CONF_NAME]
        modbus_addr = entry.data.get(CONF_MODBUS_ADDR, DEFAULT_MODBUS_ADDR)
    else:
        hub_name = entry.options[CONF_NAME]
        modbus_addr = entry.options.get(CONF_MODBUS_ADDR, DEFAULT_MODBUS_ADDR)
    hub = hass.data[DOMAIN][hub_name]["hub"]
    plugin = hub.plugin

    entities: list[SolaXModbusText] = []
    for text_info in getattr(plugin, "TEXT_TYPES", ()):
        if not (
            plugin.matchInverterWithMask(hub._invertertype, text_info.allowedtypes, hub.seriesnumber, text_info.blacklist)
            and matches_modbus_protocol(hub, text_info)
            and hub.device_group_enabled(text_info.device_group)
        ):
            continue

        device_info = hub.group_device_info(text_info.device_group) if text_info.device_group else hub.device_info

        def factory(di: Any = device_info, ti: Any = text_info) -> SolaXModbusText:
            return SolaXModbusText(hub_name, hub, modbus_addr, di, ti)

        text_entity = factory()
        hub.textEntities[text_info.key] = text_entity

        dependency_key = text_info.sensor_key or text_info.key
        if dependency_key != text_info.key:
            hub.entity_dependencies.setdefault(dependency_key, []).append(text_info.key)

        active = matches_active_when(hub, text_info)
        if text_info.active_when is not None:
            hub.register_gated_entity(text_info, factory, async_add_entities, hub.textEntities, "text", text_entity if active else None)
        if active:
            entities.append(text_entity)
        else:
            hub.textEntities.pop(text_info.key, None)

    async_add_entities(entities)
    return True


class SolaXModbusText(TextEntity):
    """Representation of a fixed-width writable Modbus text field."""

    _attr_has_entity_name = True
    entity_description: BaseModbusTextEntityDescription

    def __init__(
        self,
        platform_name: str,
        hub: Any,
        modbus_addr: int,
        device_info: DeviceInfo,
        text_info: BaseModbusTextEntityDescription,
    ) -> None:
        self._platform_name = platform_name
        self._hub = hub
        self._modbus_addr = modbus_addr
        self._attr_device_info = device_info
        self._name = text_info.name
        self._key = text_info.key
        self._register = text_info.register
        self._sensor_key = text_info.sensor_key or text_info.key
        self._wordcount = text_info.wordcount or 0
        self._write_method = text_info.write_method
        self.entity_description = text_info
        self._attr_native_value = self._read_native_value()

    def _read_native_value(self) -> str | None:
        value = self._hub.data.get(self._sensor_key)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="ignore")
        return str(value).split("\x00", 1)[0]

    async def async_added_to_hass(self) -> None:
        """Register for hub-delivered readback updates."""
        if self._register is None or self._register < 0:
            return
        await self._hub.async_add_solax_modbus_sensor(self)

    async def async_will_remove_from_hass(self) -> None:
        if self._register is None or self._register < 0:
            return
        await self._hub.async_remove_solax_modbus_sensor(self)

    @callback
    def modbus_data_updated(self) -> None:
        self._attr_native_value = self._read_native_value()
        self.async_write_ha_state()

    @property
    def name(self) -> str:
        return str(self._name or self._key)

    @property
    def unique_id(self) -> str:
        # Keep the text entity distinct from the internal readback sensor, which
        # intentionally uses the same logical key/register for the Modbus data.
        return f"{self._platform_name}_{self._key}_text"

    @property
    def should_poll(self) -> bool:
        return False

    async def async_set_value(self, value: str) -> None:
        """Validate and atomically write the complete fixed-width text field."""
        if self._read_native_value() is None:
            raise HomeAssistantError(f"{self._platform_name}: current value for {self._key} has not been read; refusing a blind write")
        if self._register is None or self._register < 0:
            raise HomeAssistantError(f"{self._platform_name}: {self._key} has no writable register")
        if self._write_method != WRITE_MULTI_MODBUS:
            raise HomeAssistantError(f"{self._platform_name}: {self._key} must use a multi-register write")

        validator = self.entity_description.value_validator
        try:
            normalized = validator(value) if validator else value
            words = _encode_fixed_width_ascii(normalized, self._wordcount)
        except (UnicodeEncodeError, ValueError) as ex:
            raise HomeAssistantError(f"{self._platform_name}: invalid value for {self._key}: {ex}") from ex

        payload = [(REGISTER_U16, word) for word in words]
        _LOGGER.info("writing %s text register %s (%s words)", self._platform_name, self._register, len(words))
        await self._hub.async_write_registers_multi(unit=self._modbus_addr, address=self._register, payload=payload)

        self._hub.data[self._sensor_key] = normalized
        self._attr_native_value = normalized
        self.async_write_ha_state()
