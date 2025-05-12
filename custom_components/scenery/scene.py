"""Scenery scene entities."""

from __future__ import annotations
from typing import Any

from homeassistant.components.scene import Scene as HassScene
from homeassistant.core import (
    HomeAssistant,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import (
    Scene,
    SceneGroup,
    async_apply_scene,
)
from .const import DOMAIN


class SceneryScene(HassScene):
    """A Scenery scene entity."""

    _attr_has_entity_name = True

    def __init__(self, scene_group: SceneGroup, scene: Scene) -> None:  # noqa: D107
        self.scene = scene
        self._attr_name = f"{scene_group.name} {scene.name}"
        if scene.unique_id is not None:
            self._attr_unique_id = f"scenery.scene.{scene.unique_id}"

    async def async_activate(self, **kwargs: Any) -> None:
        await async_apply_scene(self.hass, self.scene, reproduce_options=kwargs)


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
            SceneryScene(scene_group, scene)
            for scene_group in scenery.scene_groups
            for scene in scene_group.scenes
        ]
    )
