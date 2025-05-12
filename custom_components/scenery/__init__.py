"""The Scenery integration."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from dataclasses import dataclass
from functools import wraps
import itertools
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.components.device_automation.exceptions import EntityNotFound
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_PROFILE,
    ATTR_TRANSITION,
    Profiles,
)
from homeassistant.const import (
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_UNIQUE_ID,
    Platform,
    STATE_ON,
)
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    State,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import discovery, entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    EventEntityRegistryUpdatedData,
    async_track_entity_registry_updated_event,
)
from homeassistant.helpers.state import async_reproduce_state
from homeassistant.helpers.typing import ConfigType
from homeassistant.components.homeassistant.scene import STATES_SCHEMA
from homeassistant.util.json import JsonValueType
from homeassistant.util.read_only_dict import ReadOnlyDict

from .const import (
    CONF_FAVORITE_COLORS,
    CONF_OFF_OPTION,
    CONF_PROFILE_SELECT,
    CONF_PROFILES,
    CONF_SCENE_GROUPS,
    CONF_SCENE_SELECT,
    CONF_SCENES,
    DOMAIN,
)
from .light_utils import (
    ANY_COLOR_ATTRS,
    COLOR_SCHEMA,
    FAVORITE_COLOR_SCHEMA,
    Color,
    compare_state_to_brightness,
    compare_state_to_color,
    effective_brightness,
    extract_color,
    is_favorite_color,
    unique_colors,
)

_LOGGER = logging.getLogger(__name__)

DEBUG_SCORING = False


def _validate_domain(config: ConfigType) -> ConfigType:
    profile_names = set()
    for item in config.get(CONF_PROFILES, []):
        if (name := item[CONF_NAME]) in profile_names:
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

    scene_group_names = set()
    for scene_group_item in config.get(CONF_SCENE_GROUPS, []):
        if (scene_group_name := scene_group_item[CONF_NAME]) in scene_group_names:
            raise vol.Invalid(
                f"Scene configuration contains duplicate scene group name '{scene_group_name}'"
            )
        scene_group_names.add(scene_group_name)
        scene_names = set()
        for scene_item in scene_group_item.get(CONF_SCENES, []):
            if (scene_name := scene_item[CONF_NAME]) in scene_names:
                raise vol.Invalid(
                    f"Scene configuration contains duplicate scene '{scene_name}' in scene group '{scene_group_name}'"
                )
            scene_names.add(scene_name)

    return config


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_PROFILES): [
                        COLOR_SCHEMA.extend(
                            {
                                vol.Required(CONF_NAME): cv.string,
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
                                vol.Optional(CONF_PROFILES): [cv.string],
                                vol.Optional(CONF_FAVORITE_COLORS): [
                                    FAVORITE_COLOR_SCHEMA
                                ],
                                vol.Optional(CONF_PROFILE_SELECT): vol.All(
                                    vol.DefaultTo({}),
                                    vol.Schema(
                                        {
                                            vol.Optional(CONF_OFF_OPTION): cv.string,
                                        }
                                    ),
                                ),
                            }
                        )
                    ],
                    vol.Optional(CONF_SCENE_GROUPS): [
                        vol.Schema(
                            {
                                vol.Required(CONF_NAME): cv.string,
                                vol.Required(CONF_SCENES): [
                                    vol.Schema(
                                        {
                                            vol.Required(CONF_NAME): cv.string,
                                            vol.Required(CONF_ENTITIES): STATES_SCHEMA,
                                            vol.Optional(ATTR_TRANSITION): vol.All(
                                                vol.Coerce(float),
                                                vol.Clamp(min=0, max=6553),
                                            ),
                                            vol.Optional(CONF_UNIQUE_ID): cv.string,
                                        }
                                    )
                                ],
                                vol.Optional(CONF_SCENE_SELECT): vol.All(
                                    vol.DefaultTo({}),
                                    vol.Schema(
                                        {
                                            vol.Optional(CONF_UNIQUE_ID): cv.string,
                                        }
                                    ),
                                ),
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


@dataclass(kw_only=True)
class LightProfile:
    """A named preset that can be applied to a light."""

    name: str
    color: Color = None
    brightness: int | None = None
    transition: float | None = None

    @property
    def effective_brightness(self) -> int | None:
        return effective_brightness(self.brightness, self.color)

    def apply(self, params: dict[str, Any], set_color_and_brightness: bool = True):
        if self.transition is not None:
            params.setdefault(ATTR_TRANSITION, self.transition)
        if set_color_and_brightness:
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
            color=extract_color(config),
            brightness=config.get(ATTR_BRIGHTNESS),
            transition=config.get(ATTR_TRANSITION),
        )


@dataclass(kw_only=True)
class ProfileSelect:
    """Configures the profile select entity."""

    off_option: str | None

    @staticmethod
    def from_config(config: ConfigType) -> ProfileSelect:
        return ProfileSelect(
            off_option=label
            if (label := config.get(CONF_OFF_OPTION, "Off")) != ""
            else None,
        )


@dataclass(kw_only=True)
class LightConfig:
    """Configures how this integration will interact with a light."""

    profiles: list[LightProfile]
    favorite_colors: list[Color]
    profile_select: ProfileSelect | None

    @property
    def default_profile(self) -> LightProfile | None:
        return self.profiles[0] if self.profiles else None

    @property
    def favorite_colors_from_profiles(self) -> list[Color]:
        return [
            profile.color
            for profile in self.profiles
            if profile.color is not None and is_favorite_color(profile.color)
        ]

    @staticmethod
    def from_config(
        config: ConfigType, profiles: Mapping[str, LightProfile]
    ) -> LightConfig:
        return LightConfig(
            profiles=[profiles[name] for name in config.get(CONF_PROFILES, [])],
            favorite_colors=config.get(CONF_FAVORITE_COLORS, []),
            profile_select=ProfileSelect.from_config(c)
            if (c := config.get(CONF_PROFILE_SELECT)) is not None
            else None,
        )


ANY_CRITERION_ATTRS = [*ANY_COLOR_ATTRS, ATTR_BRIGHTNESS, ATTR_PROFILE]


@dataclass(kw_only=True)
class Criterion:
    """A parsed representation of a state that's easier to compare."""

    def __init__(self, state: State) -> None:
        attrs = state.attributes
        self.state: str = state.state  # Required
        self.color: Color | None = extract_color(attrs)
        self.brightness: int | None = attrs.get(ATTR_BRIGHTNESS)
        self.profile: str = attrs.get(ATTR_PROFILE)
        self.attributes: Mapping[str, Any] = {
            attr: value
            for attr, value in attrs.items()
            if attr not in ANY_CRITERION_ATTRS
        }


@dataclass(kw_only=True)
class Scene:
    """A named scene that applies states to entities."""

    name: str
    states: Mapping[str, State]
    criteria: Mapping[str, Criterion]
    transition: float | None
    unique_id: str | None

    @staticmethod
    def from_config(config: ConfigType) -> Scene:
        states = config.get(CONF_ENTITIES)
        return Scene(
            name=config.get(CONF_NAME),
            states=states,
            criteria={
                entity_id: Criterion(state) for entity_id, state in states.items()
            },
            transition=config.get(ATTR_TRANSITION),
            unique_id=config.get(CONF_UNIQUE_ID),
        )


@dataclass(kw_only=True)
class SceneSelect:
    """Configures the scene select entity."""

    unique_id: str | None

    @staticmethod
    def from_config(config: ConfigType) -> SceneSelect:
        return SceneSelect(
            unique_id=config.get(CONF_UNIQUE_ID),
        )


@dataclass(kw_only=True)
class SceneGroup:
    """Configures the scene group."""

    name: str
    scenes: list[Scene]
    entities: set[str]
    scene_select: SceneSelect | None

    @staticmethod
    def from_config(config: ConfigType) -> SceneGroup:
        scenes = [Scene.from_config(item) for item in config.get(CONF_SCENES)]
        return SceneGroup(
            name=config.get(CONF_NAME),
            scenes=scenes,
            entities=set(itertools.chain(*[scene.states.keys() for scene in scenes])),
            scene_select=SceneSelect.from_config(c)
            if (c := config.get(CONF_SCENE_SELECT)) is not None
            else None,
        )


class Scenery:
    """Integrates with light profiles and favorite colors."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:  # noqa: D107
        self.hass = hass
        self.light_profiles = {}
        self.light_configs = {}
        self.scene_groups = []
        self._configure(config)
        self._intercept_light_profiles()
        self._track_registry_unsub = None

    def _configure(self, config: ConfigType) -> None:
        for item in config.get(CONF_PROFILES, []):
            light_profile = LightProfile.from_config(item)
            self.light_profiles[light_profile.name] = light_profile
        for item in config.get(CONF_LIGHTS, []):
            light_config = LightConfig.from_config(item, self.light_profiles)
            for entity_id in item[CONF_ENTITY_ID]:
                self.light_configs[entity_id] = light_config
        for item in config.get(CONF_SCENE_GROUPS, []):
            scene_group = SceneGroup.from_config(item)
            for scene in scene_group.scenes:
                self._apply_profiles_to_scene_states(scene)
            self.scene_groups.append(scene_group)

    def _apply_profiles_to_scene_states(self, scene: Scene):
        # Apply the definition of light profiles to the scene's states ahead of time because
        # the light platform ignores the profile attribute when reproducing a state.
        for state in scene.states.values():
            profile_name = state.attributes.get(ATTR_PROFILE)
            if profile_name is not None:
                profile = self.light_profiles.get(profile_name)
                if profile is not None:
                    new_attributes = {
                        k: v for k, v in state.attributes.items() if k != ATTR_PROFILE
                    }
                    profile.apply(
                        new_attributes,
                        set_color_and_brightness=state.state == STATE_ON,
                    )
                    state.attributes = ReadOnlyDict(new_attributes)
                else:
                    _LOGGER.warning(
                        "Scene '%s' references undefined light profile '%s'",
                        scene.name,
                        profile_name,
                    )

    def _intercept_light_profiles(self) -> None:
        def apply_default(
            entity_id: str, state_on: bool | None, params: dict[str, Any]
        ) -> bool:
            return (
                (config := self.light_configs.get(entity_id)) is not None
                and (profile := config.default_profile) is not None
                and profile.apply(params, not state_on or not params)
            )

        def apply_light_profile(
            name: str,
            params: dict[str, Any],
        ) -> bool:
            return (
                profile := self.light_profiles.get(name)
            ) is not None and profile.apply(params)

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
            if not apply_light_profile(name, params):
                _profiles_apply_profile(self, name, params)

        Profiles.apply_default = _handle_apply_default
        Profiles.apply_profile = _handle_apply_profile

    def async_setup(self) -> None:  # noqa: D102
        self._track_registry_unsub = async_track_entity_registry_updated_event(
            self.hass,
            self.light_configs.keys(),
            self._handle_registry_updated_event,
        )
        for entity_id in self.light_configs:
            self._try_set_favorite_colors(entity_id)

    @callback
    def _handle_registry_updated_event(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        if event.data["action"] == "create":
            self._try_set_favorite_colors(event.data["entity_id"])

    def _try_set_favorite_colors(self, entity_id: str) -> None:
        light_config = self.light_configs[entity_id]
        with contextlib.suppress(EntityNotFound):
            async_set_favorite_colors(
                self.hass,
                entity_id,
                unique_colors(
                    [
                        *light_config.favorite_colors_from_profiles,
                        *light_config.favorite_colors,
                    ]
                ),
            )


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

    scenery.async_setup()

    discovery.load_platform(hass, Platform.SCENE, DOMAIN, {}, config)
    discovery.load_platform(hass, Platform.SELECT, DOMAIN, {}, config)
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


async def async_turn_off(
    hass: HomeAssistant,
    entity_id: str,
    blocking: bool = False,
) -> None:
    """Turn off a light with the specified profile."""
    await hass.services.async_call(
        "light",
        "turn_off",
        {
            "entity_id": entity_id,
        },
        blocking=blocking,
    )


async def async_turn_on(
    hass: HomeAssistant,
    entity_id: str,
    profile: str,
    blocking: bool = False,
) -> None:
    """Turn on a light with the specified profile."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": entity_id,
            "profile": profile,
        },
        blocking=blocking,
    )


def guess_profile(
    light_attrs: Mapping[str, Any], candidates: list[LightProfile]
) -> LightProfile | None:
    """Guess which light profile is active by comparing light attributes."""
    candidates = [
        (_rank_profile(light_attrs, profile), profile) for profile in candidates
    ]
    candidates.sort(key=lambda x: x[0], reverse=True)
    if DEBUG_SCORING:
        _LOGGER.debug("Profile scores: %s", repr(candidates))
    return candidates[0][1] if candidates and candidates[0][0] > 0 else None


def _rank_profile(light_attrs: Mapping[str, Any], profile: LightProfile) -> int:
    score = 0
    # Color is considered an intrinsic part of the light profile.
    # If specified, then it must match the state.
    if profile.color is not None:
        if not compare_state_to_color(light_attrs, profile.color):
            return 0
        score += 2
    # Brightness is considered a more flexible part of the light profile
    # to allow users to adjust it for their comfort without disrupting the state.
    # If specified and it matches the state then rank the profile higher.
    if (profile_brightness := profile.effective_brightness) is not None:
        if compare_state_to_brightness(light_attrs, profile_brightness):
            score += 1
    return score


async def async_apply_scene(
    hass: HomeAssistant, scene: Scene, reproduce_options: dict[str, Any] | None = None
) -> None:
    """Applies a scene."""
    new_options = {}
    if scene.transition is not None:
        new_options[ATTR_TRANSITION] = scene.transition
    if reproduce_options is not None:
        new_options.update(reproduce_options)
    await async_reproduce_state(
        hass, scene.states.values(), reproduce_options=new_options
    )


def guess_scene(
    scenery: Scenery, states: Mapping[str, State], candidates: list[Scene]
) -> Scene | None:
    """Guess which scene is active by comparing state attributes."""
    profiles = {
        id: profile.name
        for id, state in states.items()
        if id in scenery.light_configs
        and (
            profile := guess_profile(
                state.attributes, scenery.light_configs[id].profiles
            )
        )
        is not None
    }
    candidates = [(_rank_scene(states, profiles, scene), scene) for scene in candidates]
    candidates.sort(key=lambda x: x[0], reverse=True)
    if DEBUG_SCORING:
        _LOGGER.debug("Scene scores: %s", repr(candidates))
    return candidates[0][1] if candidates and candidates[0][0] > 0 else None


def _rank_scene(
    states: Mapping[str, Any], profiles: Mapping[str, str], scene: Scene
) -> int:
    score = 0
    for entity_id, criterion in scene.criteria.items():
        state = states.get(entity_id)
        if not state:
            continue  # Ignore the state of unavailable entities
        if criterion.state != state.state:
            if DEBUG_SCORING:
                _LOGGER.debug(
                    "%s / %s: failed state criterion %s, actual %s",
                    scene.name,
                    entity_id,
                    criterion.state,
                    state.state,
                )
            return 0
        score += 1
        if criterion.profile is not None:
            if profiles.get(entity_id) != criterion.profile:
                if DEBUG_SCORING:
                    _LOGGER.debug(
                        "%s / %s: failed profile criterion %s, actual %s",
                        scene.name,
                        entity_id,
                        criterion.profile,
                        profiles.get(entity_id),
                    )
                return 0
            score += 1
        if criterion.color is not None:
            if not compare_state_to_color(state.attributes, criterion.color):
                if DEBUG_SCORING:
                    _LOGGER.debug(
                        "%s / %s: failed color criterion %s, attributes %s",
                        scene.name,
                        entity_id,
                        repr(criterion.color),
                        repr(state.attributes),
                    )
                return 0
            score += 1
        if criterion.brightness is not None:
            if not compare_state_to_brightness(state.attributes, criterion.brightness):
                if DEBUG_SCORING:
                    _LOGGER.debug(
                        "%s / %s: failed brightness criterion %s, attributes %s",
                        scene.name,
                        entity_id,
                        repr(criterion.brightness),
                        repr(state.attributes),
                    )
                return 0
            score += 1
        for key, value in criterion.attributes.items():
            if value != state.attributes.get(key):
                if DEBUG_SCORING:
                    _LOGGER.debug(
                        "%s / %s: failed key %s criterion %s, value %s",
                        scene.name,
                        entity_id,
                        key,
                        value,
                        state.attributes.get(key),
                    )
                return 0
            score += 1
    return score
