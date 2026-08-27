"""Tests for entity-description loading decisions."""

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.solax_modbus import should_register_be_loaded
from custom_components.solax_modbus.const import BaseModbusSensorEntityDescription, BaseModbusSwitchEntityDescription


def test_descriptor_without_internal_is_loaded_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control descriptions without an internal field must not stop polling."""
    descriptor = BaseModbusSwitchEntityDescription(key="test_switch")
    hub = SimpleNamespace(name="Test hub", _name="Test hub")
    registry = SimpleNamespace(async_get_entity_id=lambda *_args: None)
    fake_hass: Any = object()
    monkeypatch.setattr(
        "custom_components.solax_modbus.er.async_get",
        lambda _hass: registry,
    )

    assert not hasattr(descriptor, "internal")
    assert should_register_be_loaded(fake_hass, hub, descriptor)


@pytest.mark.parametrize(("disabled", "expected"), [(False, True), (True, False)])
def test_sensitive_internal_readback_follows_text_entity_state(
    monkeypatch: pytest.MonkeyPatch,
    disabled: bool,
    expected: bool,
) -> None:
    """Conditional internal readbacks poll only while their text control is enabled."""
    descriptor = BaseModbusSensorEntityDescription(
        key="cloud_endpoint",
        internal=True,
        internal_requires_control=True,
        entity_registry_enabled_default=False,
    )
    hub = SimpleNamespace(
        name="Test hub",
        _name="Test hub",
        selectEntities={},
        numberEntities={},
        switchEntities={},
        timeEntities={},
        textEntities={},
    )

    def entity_id(platform: Any, _domain: str, _unique_id: str) -> str | None:
        return "text.test_hub_cloud_endpoint" if str(platform) == "text" else None

    registry = SimpleNamespace(
        async_get_entity_id=entity_id,
        async_get=lambda _entity_id: SimpleNamespace(disabled=disabled),
    )
    fake_hass: Any = object()
    monkeypatch.setattr("custom_components.solax_modbus.er.async_get", lambda _hass: registry)

    assert should_register_be_loaded(fake_hass, hub, descriptor) is expected
