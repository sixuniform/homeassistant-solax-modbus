"""Tests for Solinteg cloud and technical endpoint settings."""

from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.solax_modbus.const import REGISTER_STR, REGISTER_U16
from custom_components.solax_modbus.plugin_solinteg import SENSOR_TYPES, TEXT_TYPES, _normalize_endpoint
from custom_components.solax_modbus.text import SolaXModbusText, _encode_fixed_width_ascii


def test_cloud_endpoint_settings_are_disabled_fixed_width_text() -> None:
    descriptions = {description.key: description for description in TEXT_TYPES}

    assert descriptions.keys() == {"cloud_endpoint", "technical_service_endpoint"}
    assert descriptions["cloud_endpoint"].register == 20016
    assert descriptions["technical_service_endpoint"].register == 20046
    assert all(description.register_data_type == REGISTER_STR for description in descriptions.values())
    assert all(description.wordcount == 30 for description in descriptions.values())
    assert all(description.entity_registry_enabled_default is False for description in descriptions.values())


def test_cloud_endpoint_readbacks_activate_only_with_controls() -> None:
    endpoint_keys = {"cloud_endpoint", "technical_service_endpoint"}
    descriptions = {description.key: description for description in SENSOR_TYPES if description.key in endpoint_keys}

    assert descriptions.keys() == endpoint_keys
    assert all(description.internal for description in descriptions.values())
    assert all(description.internal_requires_control for description in descriptions.values())
    assert all(description.entity_registry_enabled_default is False for description in descriptions.values())


def test_solinteg_endpoint_validation_accepts_hostnames_and_ipv4() -> None:
    assert _normalize_endpoint("5743,iot.solinteg-cloud.com") == "5743,iot.solinteg-cloud.com"
    assert _normalize_endpoint("5743,192.168.10.50") == "5743,192.168.10.50"


def test_solinteg_endpoint_validation_rejects_unsafe_values() -> None:
    for value in (
        "5743",
        "0,example.com",
        "65536,example.com",
        "5743,bad_host",
        "5743,192.168.010.050",
        "5743,2001:db8::1",
    ):
        try:
            _normalize_endpoint(value)
        except ValueError:
            continue
        raise AssertionError(f"unsafe endpoint was accepted: {value}")


def test_fixed_width_ascii_encoding_is_exact_and_nul_terminated() -> None:
    words = _encode_fixed_width_ascii("5743,192.168.10.50", 30)
    encoded = b"".join(word.to_bytes(2, "big") for word in words)

    assert len(words) == 30
    assert len(encoded) == 60
    assert encoded.startswith(b"5743,192.168.10.50\x00")
    assert encoded[len(b"5743,192.168.10.50") :] == b"\x00" * (60 - len(b"5743,192.168.10.50"))


@pytest.mark.asyncio
async def test_cloud_endpoint_entity_writes_one_exact_register_span(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHub:
        def __init__(self) -> None:
            self.data = {"cloud_endpoint": "5743,iot.solinteg-cloud.com"}
            self.calls: list[dict[str, Any]] = []

        async def async_write_registers_multi(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    hub = FakeHub()
    description = next(description for description in TEXT_TYPES if description.key == "cloud_endpoint")
    entity = SolaXModbusText(
        "SolaX",
        hub,
        255,
        DeviceInfo(identifiers={("solax_modbus", "test")}),
        description,
    )
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: None)

    await entity.async_set_value("5743,192.168.10.50")

    assert len(hub.calls) == 1
    call = hub.calls[0]
    assert call["unit"] == 255
    assert call["address"] == 20016
    assert len(call["payload"]) == 30
    encoded = b"".join(value.to_bytes(2, "big") for register_type, value in call["payload"] if register_type == REGISTER_U16)
    assert encoded == b"5743,192.168.10.50\x00".ljust(60, b"\x00")
    assert hub.data["cloud_endpoint"] == "5743,192.168.10.50"


@pytest.mark.asyncio
async def test_cloud_endpoint_entity_refuses_blind_write(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHub:
        data: dict[str, Any] = {}

        async def async_write_registers_multi(self, **_kwargs: Any) -> None:
            raise AssertionError("write must not be attempted")

    description = next(description for description in TEXT_TYPES if description.key == "cloud_endpoint")
    entity = SolaXModbusText(
        "SolaX",
        FakeHub(),
        255,
        DeviceInfo(identifiers={("solax_modbus", "test")}),
        description,
    )
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: None)

    with pytest.raises(HomeAssistantError, match="refusing a blind write"):
        await entity.async_set_value("5743,192.168.10.50")
