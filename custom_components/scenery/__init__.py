"""The Scenery integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.device_automation.exceptions import EntityNotFound
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_TRANSITION,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    Profiles,
)
from homeassistant.const import CONF_ENTITY_ID, CONF_LIGHTS, CONF_NAME
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

from .const import CONF_FAVORITE_COLORS, CONF_PROFILES, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Color attributes that can be used as favorite colors by the frontend
FAVORITE_COLOR_ATTRS = (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
)

# All color attributes that the backend supports
ANY_COLOR_ATTRS = (
    *FAVORITE_COLOR_ATTRS,
    ATTR_XY_COLOR,
    ATTR_WHITE,
    ATTR_COLOR_NAME,
    # _DEPRECATED_ATTR_COLOR_TEMP.value,
    # _DEPRECATED_ATTR_KELVIN.value,
)

type Color = Mapping[str, Any]


COLOR_GROUP = "color"
COLOR_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_COLOR_TEMP_KELVIN, COLOR_GROUP): cv.positive_int,
        vol.Exclusive(ATTR_HS_COLOR, COLOR_GROUP): vol.All(
            vol.Coerce(tuple),
            vol.ExactSequence(
                (
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=360)),
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                )
            ),
        ),
        vol.Exclusive(ATTR_RGB_COLOR, COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 3)
        ),
        vol.Exclusive(ATTR_RGBW_COLOR, COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 4)
        ),
        vol.Exclusive(ATTR_RGBWW_COLOR, COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 5)
        ),
        vol.Exclusive(ATTR_XY_COLOR, COLOR_GROUP): vol.All(
            vol.Coerce(tuple), vol.ExactSequence((cv.small_float, cv.small_float))
        ),
        vol.Exclusive(ATTR_WHITE, COLOR_GROUP): vol.All(
            vol.Coerce(int), vol.Clamp(min=0, max=255)
        ),
        vol.Exclusive(ATTR_COLOR_NAME, COLOR_GROUP): cv.string,
    }
)


def _has_one_attr(value: ConfigType, attrs: tuple[str]) -> bool:
    keys = list(value.keys())
    return len(keys) == 1 and keys[0] in attrs


def _get_color(value: ConfigType) -> Color | None:
    color = {attr: value[attr] for attr in ANY_COLOR_ATTRS if attr in value}
    return color if color != {} else None


def _is_favorite_color(value: ConfigType) -> bool:
    return _has_one_attr(value, FAVORITE_COLOR_ATTRS)


def _validate_favorite_color(value: ConfigType) -> Color:
    if not _is_favorite_color(value):
        raise vol.Invalid(f"Must specify one of {FAVORITE_COLOR_ATTRS}")
    return value


def _deduplicate_colors(colors: list[Color]) -> list[Color]:
    result = []
    for color in colors:
        if color not in result:
            result.append(color)
    return result


FAVORITE_COLOR_SCHEMA = vol.All(_validate_favorite_color, COLOR_SCHEMA)


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
        vol.Optional(CONF_FAVORITE_COLORS): [FAVORITE_COLOR_SCHEMA],
    },
)


def _validate_domain(config: ConfigType) -> ConfigType:
    profile_names = set()
    for item in config.get(CONF_PROFILES, []):
        name = item[CONF_NAME]
        if name in profile_names:
            raise vol.Invalid(
                f"Profile configuration contains duplicate profile name '{name}'"
            )
        profile_names.add(name)

    entity_ids = set()
    for item in config.get(CONF_LIGHTS, []):
        for entity_id in item[CONF_ENTITY_ID]:
            if entity_id in entity_ids:
                raise vol.Invalid(
                    f"Light configuration contains duplicate entity ID '{entity_id}'"
                )
            entity_ids.add(entity_id)
        for name in item.get(CONF_PROFILES, []):
            if name not in profile_names:
                raise vol.Invalid(
                    f"Light configuration contains unknown profile name '{name}'"
                )
    return config


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_PROFILES): [
                        COLOR_SCHEMA.extend(
                            {
                                vol.Required(CONF_NAME): str,
                                vol.Optional(ATTR_BRIGHTNESS): vol.All(
                                    vol.Coerce(int), vol.Clamp(min=0, max=255)
                                ),
                                vol.Optional(ATTR_TRANSITION): vol.All(
                                    vol.Coerce(float), vol.Clamp(min=0, max=6553)
                                ),
                            }
                        )
                    ],
                    vol.Optional(CONF_LIGHTS): [
                        vol.Schema(
                            {
                                vol.Required(CONF_ENTITY_ID): cv.entity_ids,
                                vol.Optional(CONF_PROFILES): [str],
                                vol.Optional(CONF_FAVORITE_COLORS): [
                                    FAVORITE_COLOR_SCHEMA
                                ],
                            }
                        )
                    ],
                }
            ),
            _validate_domain,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class LightProfile:
    name: str
    color: Color = None
    brightness: int | None = None
    transition: int | None = None

    def apply(self, state_on: bool | None, params: dict[str, Any]):
        if self.transition is not None:
            params.setdefault(ATTR_TRANSITION, self.transition)
        if not state_on or not params:
            if self.brightness is not None:
                params.setdefault(ATTR_BRIGHTNESS, self.brightness)
            if self.color is not None and not any(
                attr in params for attr in ANY_COLOR_ATTRS
            ):
                params.update(self.color)

    @staticmethod
    def from_config(config: ConfigType) -> LightProfile:
        return LightProfile(
            name=config[CONF_NAME],
            color=_get_color(config),
            brightness=config.get(ATTR_BRIGHTNESS),
            transition=config.get(ATTR_TRANSITION),
        )


@dataclass
class LightConfig:
    profiles: list[LightProfile]
    favorite_colors: list[Color]

    @property
    def default_profile(self) -> LightProfile | None:
        return self.profiles[0] if self.profiles else None

    @property
    def favorite_colors_from_profiles(self) -> list[Color]:
        return [
            profile.color
            for profile in self.profiles
            if _is_favorite_color(profile.color)
        ]

    @staticmethod
    def from_config(
        config: ConfigType, profiles: Mapping[str, LightProfile]
    ) -> LightConfig:
        return LightConfig(
            profiles=[profiles[name] for name in config.get(CONF_PROFILES, [])],
            favorite_colors=config.get(CONF_FAVORITE_COLORS, []),
        )


class Scenery:
    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:  # noqa: D107
        self.hass = hass
        self.light_profiles = {}
        self.light_configs = {}
        self._configure(config)
        self._intercept_light_profiles()

    def _configure(self, config: ConfigType) -> None:
        for item in config.get(CONF_PROFILES, []):
            light_profile = LightProfile.from_config(item)
            self.light_profiles[light_profile.name] = light_profile
        for item in config.get(CONF_LIGHTS, []):
            light_config = LightConfig.from_config(item, self.light_profiles)
            for entity_id in item[CONF_ENTITY_ID]:
                self.light_configs[entity_id] = light_config

    def _intercept_light_profiles(self) -> None:
        def apply_default(
            entity_id: str, state_on: bool | None, params: dict[str, Any]
        ) -> bool:
            return (
                (config := self.light_configs.get(entity_id)) is not None
                and (profile := config.default_profile) is not None
                and profile.apply(state_on, params)
            )

        def apply_light_profile(
            name: str,
            state_on: bool | None,
            params: dict[str, Any],
        ) -> bool:
            return (
                profile := self.light_profiles.get(name)
            ) is not None and profile.apply(state_on, params)

        _profiles_apply_default = Profiles.apply_default
        _profiles_apply_profile = Profiles.apply_profile

        @wraps(_profiles_apply_default)
        def _handle_apply_default(
            self, entity_id: str, state_on: bool | None, params: dict[str, Any]
        ) -> None:
            if not apply_default(entity_id, state_on, params):
                _profiles_apply_default(self, entity_id, state_on, params)

        @wraps(_profiles_apply_profile)
        def _handle_apply_profile(self, name: str, params: dict[str, Any]) -> None:
            if not apply_light_profile(name, False, params):
                _profiles_apply_profile(self, name, params)

        Profiles.apply_default = _handle_apply_default
        Profiles.apply_profile = _handle_apply_profile


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    scenery = hass.data[DOMAIN] = Scenery(hass, config[DOMAIN])

    async def _handle_get_favorite_colors(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[CONF_ENTITY_ID]
        colors = async_get_favorite_colors(hass, entity_id)
        return {CONF_FAVORITE_COLORS: cast(list[JsonValueType], colors)}

    hass.services.async_register(
        DOMAIN,
        GET_FAVORITE_COLORS_SERVICE,
        _handle_get_favorite_colors,
        schema=GET_FAVORITE_COLORS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _handle_set_favorite_colors(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data[CONF_ENTITY_ID]
        colors = call.data.get(CONF_FAVORITE_COLORS)
        async_set_favorite_colors(hass, entity_id, colors)
        return None

    hass.services.async_register(
        DOMAIN,
        SET_FAVORITE_COLORS_SERVICE,
        _handle_set_favorite_colors,
        schema=SET_FAVORITE_COLORS_SCHEMA,
    )

    for entity_id, light_config in scenery.light_configs.items():
        async_set_favorite_colors(
            hass,
            entity_id,
            _deduplicate_colors(
                [
                    *light_config.favorite_colors_from_profiles,
                    *light_config.favorite_colors,
                ]
            ),
        )

    return True


def async_get_favorite_colors(
    hass: HomeAssistant, entity_id: str
) -> list[Color] | None:
    """Get the favorite colors of a light entity."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        raise EntityNotFound(f"Entity ID {entity_id} is not valid")

    if (options := entity.options.get("light")) is None:
        return None
    if (colors := options.get("favorite_colors")) is None:
        return None
    return colors


def async_set_favorite_colors(
    hass: HomeAssistant, entity_id: str, colors: list[Color] | None
) -> None:
    """Get the favorite colors of a light entity."""
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
