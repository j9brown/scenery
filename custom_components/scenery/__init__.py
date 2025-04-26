"""The Scenery integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.device_automation.exceptions import EntityNotFound
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.json import JsonValueType

from .const import CONF_COLORS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({})},
    extra=vol.ALLOW_EXTRA,
)

type LightColor = Mapping[str, Any]
LIGHT_COLOR_KEYS = [
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
]


def _validate_light_color_key(value: LightColor) -> LightColor:
    keys = list(value.keys())
    if len(keys) != 1 or keys[0] not in LIGHT_COLOR_KEYS:
        raise vol.Invalid(f"Must specify one of {LIGHT_COLOR_KEYS}")
    return value


COLOR_GROUP = "color"
LIGHT_COLOR_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Exclusive("color_temp_kelvin", COLOR_GROUP): cv.positive_int,
            vol.Exclusive("hs_color", COLOR_GROUP): vol.All(
                vol.Coerce(tuple),
                vol.ExactSequence(
                    (
                        vol.All(vol.Coerce(float), vol.Range(min=0, max=360)),
                        vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                    )
                ),
            ),
            vol.Exclusive("rgb_color", COLOR_GROUP): vol.All(
                vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 3)
            ),
            vol.Exclusive("rgbw_color", COLOR_GROUP): vol.All(
                vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 4)
            ),
            vol.Exclusive("rgbww_color", COLOR_GROUP): vol.All(
                vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 5)
            ),
        }
    ),
    _validate_light_color_key,
)


GET_FAVORITE_COLORS_SERVICE = "get_favorite_colors"
GET_FAVORITE_COLORS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
    },
)

SET_FAVORITE_COLORS_SERVICE = "set_favorite_colors"
SET_FAVORITE_COLORS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_COLORS): [LIGHT_COLOR_SCHEMA],
    },
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""

    async def _handle_get_favorite_colors(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[CONF_ENTITY_ID]
        colors = await _get_favorite_colors(hass, entity_id)
        return {CONF_COLORS: cast(list[JsonValueType], colors)}

    hass.services.async_register(
        DOMAIN,
        GET_FAVORITE_COLORS_SERVICE,
        _handle_get_favorite_colors,
        schema=GET_FAVORITE_COLORS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _handle_set_favorite_colors(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[CONF_ENTITY_ID]
        colors = call.data.get(CONF_COLORS)
        await _set_favorite_colors(hass, entity_id, colors)
        return None

    hass.services.async_register(
        DOMAIN,
        SET_FAVORITE_COLORS_SERVICE,
        _handle_set_favorite_colors,
        schema=SET_FAVORITE_COLORS_SCHEMA,
    )
    return True


async def _get_favorite_colors(
    hass: HomeAssistant, entity_id: str
) -> list[LightColor] | None:
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        raise EntityNotFound(f"Entity ID {entity_id} is not valid")

    if (options := entity.options.get("light")) is None:
        return None
    if (colors := options.get("favorite_colors")) is None:
        return None
    return colors


async def _set_favorite_colors(
    hass: HomeAssistant, entity_id: str, colors: list[LightColor] | None
) -> None:
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        raise EntityNotFound(f"Entity ID {entity_id} is not valid")

    if (old_options := entity.options.get("light")) is None:
        if colors is None:
            return
        options = {}
    else:
        options = {k: v for k, v in old_options.items() if k != "favorite_colors"}
    if colors is not None:
        options["favorite_colors"] = colors

    er.async_get(hass).async_update_entity_options(entity_id, "light", options)
