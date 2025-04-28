"""Utilities for manipulating light attributes.

Refer to https://developers.home-assistant.io/docs/core/entity/light/
"""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    ColorMode,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import color as color_util

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

# Colors are mappings from light attributes to values.
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


def is_favorite_color(value: ConfigType) -> bool:
    return _has_one_attr(value, FAVORITE_COLOR_ATTRS)


def validate_favorite_color(value: ConfigType) -> Color:
    if not is_favorite_color(value):
        raise vol.Invalid(f"Must specify one of {FAVORITE_COLOR_ATTRS}")
    return value


def extract_color(value: ConfigType) -> Color | None:
    color = {attr: value[attr] for attr in ANY_COLOR_ATTRS if attr in value}
    return color if color != {} else None


def unique_colors(colors: list[Color]) -> list[Color]:
    result = []
    for color in colors:
        if color not in result:
            result.append(color)
    return result


FAVORITE_COLOR_SCHEMA = vol.All(validate_favorite_color, COLOR_SCHEMA)


TOLERANCE_HUE = 2
TOLERANCE_SATURATION = 2
TOLERANCE_PRIMARY = 2
TOLERANCE_CHROMATICITY = 0.05
TOLERANCE_KELVIN = 100
TOLERANCE_BRIGHTNESS = 2


def compare_hue(a: any, b: any) -> bool:  # range 0 to 360
    delta = float(a) - float(b)
    return delta % 360 < TOLERANCE_HUE or -delta % 360 <= TOLERANCE_HUE


def compare_saturation(a: any, b: any) -> bool:  # range 0 to 100
    return abs(float(a) - float(b)) <= TOLERANCE_SATURATION


def compare_primary(a: any, b: any) -> bool:  # range 0 to 255
    return abs(int(a) - int(b)) <= TOLERANCE_PRIMARY


def compare_chromaticity(a: any, b: any) -> bool:  # range 0 to 1
    return abs(float(a) - float(b)) <= TOLERANCE_CHROMATICITY


def compare_kelvin(a: any, b: any) -> bool:  # range 2000 to 6800
    return abs(int(a) - int(b)) <= TOLERANCE_KELVIN


def compare_brightness(a: any, b: any) -> bool:  # range 0 to 255
    return abs(int(a) - int(b)) <= TOLERANCE_BRIGHTNESS


def compare_state_to_color(a: Mapping[str, Any], b: Color) -> bool:
    """Compare colors for approximate equivalence.

    The comparison is not symmetric. It tests whether one of `a`'s color attributes
    matches the attribute in `b`. When comparing a light state (which may have several
    equivalent representations) to a color, set `a` to the light state's attributes.
    """
    if (b_color_name := b.get(ATTR_COLOR_NAME)) is not None:
        b = {ATTR_RGB_COLOR: color_util.color_name_to_rgb(b_color_name)}
    if b.get(ATTR_WHITE) is not None:
        return a.get(ATTR_COLOR_MODE) == ColorMode.WHITE

    if (
        (a_kelvin := a.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        and (b_kelvin := b.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        and compare_kelvin(a_kelvin, b_kelvin)
    ):
        return True
    if (
        (a_rgbww := a.get(ATTR_RGBWW_COLOR)) is not None
        and (b_rgbww := b.get(ATTR_RGBWW_COLOR)) is not None
        and all(compare_primary(a_rgbww[i], b_rgbww[i]) for i in range(5))
    ):
        return True
    if (
        (a_rgbw := a.get(ATTR_RGBW_COLOR)) is not None
        and (b_rgbw := b.get(ATTR_RGBW_COLOR)) is not None
        and all(compare_primary(a_rgbw[i], b_rgbw[i]) for i in range(4))
    ):
        return True
    if (
        (a_rgb := a.get(ATTR_RGB_COLOR)) is not None
        and (b_rgb := b.get(ATTR_RGB_COLOR)) is not None
        and all(compare_primary(a_rgb[i], b_rgb[i]) for i in range(3))
    ):
        return True
    if (a_hs := a.get(ATTR_HS_COLOR)) is not None and (
        (b_hs := b.get(ATTR_HS_COLOR)) is not None
        and compare_hue(a_hs[0], b_hs[0])
        and compare_saturation(a_hs[1], b_hs[1])
    ):
        return True
    if (
        (a_xy := a.get(ATTR_XY_COLOR)) is not None
        and (b_xy := b.get(ATTR_XY_COLOR)) is not None
        and all(compare_chromaticity(a_xy[i], b_xy[i]) for i in range(2))
    ):
        return True

    return False


def compare_state_to_brightness(a: Mapping[str, Any], b_brightness: int) -> bool:
    return (a_brightness := a.get(ATTR_BRIGHTNESS)) is not None and compare_brightness(
        a_brightness, b_brightness
    )


def effective_brightness(brightness: int | None, color: Color | None) -> int | None:
    if brightness is not None:
        return brightness
    if color is not None and (white := color.get(ATTR_WHITE)) is not None:
        return white
    return None
