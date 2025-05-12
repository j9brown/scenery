"""Scenery select entities."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import (
    EventEntityRegistryUpdatedData,
    LightConfig,
    SceneGroup,
    Scenery,
    async_apply_scene,
    async_turn_off,
    async_turn_on,
    guess_profile,
    guess_scene,
)
from .const import DOMAIN


class SceneryLightProfileSelectEntity(SelectEntity):
    """Selects and activates a profile for a light."""

    _attr_has_entity_name = True

    def __init__(self, light_entity_id: str, light_config: LightConfig) -> None:  # noqa: D107
        self.light_entity_id = light_entity_id
        self.light_config = light_config
        self.off_option = self.light_config.profile_select.off_option
        self.entity_description = SelectEntityDescription(
            key="profile",
            name="Profile",
            icon="mdi:palette",
            options=[
                *[profile.name for profile in light_config.profiles],
                *([self.off_option] if self.off_option is not None else []),
            ],
        )
        self._attr_unique_id = (
            f"scenery.{light_entity_id}.{self.entity_description.key}"
        )
        self._attr_current_option = None
        self._attr_should_poll = False
        self._set_default_name()

    async def async_select_option(self, option: str) -> None:  # noqa: D102
        if option == self.off_option:
            await async_turn_off(self.hass, self.light_entity_id, blocking=True)
        else:
            await async_turn_on(self.hass, self.light_entity_id, option, blocking=True)

    async def async_added_to_hass(self) -> None:  # noqa: D102
        self.async_on_remove(
            async_track_entity_registry_updated_event(
                self.hass,
                self.light_entity_id,
                self._handle_registry_updated_event,
            )
        )
        self._async_update_name()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.light_entity_id, self._handle_light_state_change_event
            )
        )
        self._async_update_from_light_state(self.hass.states.get(self.light_entity_id))

    @callback
    def _handle_registry_updated_event(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        self._async_update_name()

    def _async_update_name(self):
        if (
            light_entity := er.async_get(self.hass).async_get(self.light_entity_id)
        ) is not None and (
            light_name := light_entity.name or light_entity.original_name
        ) is not None:
            self._attr_name = f"{light_name} {self.entity_description.name}"
        else:
            self._set_default_name()
        self.async_write_ha_state()

    def _set_default_name(self):
        self._attr_name = f"{self.light_entity_id.removeprefix('light.')} {self.entity_description.name}"

    @callback
    def _handle_light_state_change_event(
        self, event: Event[EventStateChangedData]
    ) -> None:
        self._async_update_from_light_state(event.data.get("new_state"))

    def _async_update_from_light_state(self, state: State | None) -> None:
        self._attr_current_option = None
        self._attr_available = False
        if state is not None and state.domain == "light":
            if state.state == STATE_OFF:
                self._attr_current_option = self.off_option
                self._attr_available = True
            elif state.state == STATE_ON:
                profile = guess_profile(state.attributes, self.light_config.profiles)
                self._attr_current_option = (
                    profile.name if profile is not None else None
                )
                self._attr_available = True
        self.async_write_ha_state()


class ScenerySceneSelectEntity(SelectEntity):
    """Selects and activates a scene."""

    _attr_has_entity_name = True

    def __init__(self, scenery: Scenery, scene_group: SceneGroup) -> None:  # noqa: D107
        self.scenery = scenery
        self.scene_group = scene_group
        self.entity_description = SelectEntityDescription(
            key="scene",
            name="Scene",
            icon="mdi:palette",
            options=[scene.name for scene in scene_group.scenes],
        )
        if scene_group.scene_select.unique_id is not None:
            self._attr_unique_id = (
                f"scenery.scene_select.{scene_group.scene_select.unique_id}"
            )
        self._attr_current_option = None
        self._attr_should_poll = False
        self._attr_name = scene_group.name

    async def async_select_option(self, option: str) -> None:  # noqa: D102
        scene = self.scene_group.scenes[self.options.index(option)]
        await async_apply_scene(self.hass, scene)

    async def async_added_to_hass(self) -> None:  # noqa: D102
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.scene_group.entities, self._handle_state_change_event
            )
        )
        self._async_update()

    @callback
    def _handle_state_change_event(self, event: Event[EventStateChangedData]) -> None:
        self._async_update()

    def _async_update(self) -> None:
        states = {
            entity_id: state
            for entity_id in self.scene_group.entities
            if (state := self.hass.states.get(entity_id)) is not None
        }
        if states:
            scene = guess_scene(self.scenery, states, self.scene_group.scenes)
            self._attr_current_option = scene.name if scene is not None else None
            self._attr_available = True
        else:
            self._attr_current_option = None
            self._attr_available = False
        self.async_write_ha_state()


def setup_platform(  # noqa: D103
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    # We only want this platform to be set up via discovery.
    if discovery_info is None:
        return
    scenery = hass.data[DOMAIN]
    add_entities(
        [
            *(
                SceneryLightProfileSelectEntity(light_entity_id, light_config)
                for light_entity_id, light_config in scenery.light_configs.items()
                if light_config.profile_select is not None
            ),
            *(
                ScenerySceneSelectEntity(scenery, item)
                for item in scenery.scene_groups
                if item.scene_select is not None
            ),
        ]
    )
