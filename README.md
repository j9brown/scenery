# Scenery for Home Assistant

Coordinate light profiles and favorite colors centrally.

## YAML Configuration

### Scenery element

Add the `scenery` element to your `configuration.yaml` to configure the Scenery integration and enable its services.

| Attribute | Optional | Description |
| --------- | -------- |------------ |
| profiles  | yes      | List of light profile elements. |
| lights    | yes      | List of light configuration elements. |

```yaml
# Configure the scenery integration
scenery:
  profiles:
    - ...
  lights:
    - ...
```

### Light profile element

Each element of the `profiles` list defines a light profile.  A light profile has a unique name and a set of lighting attributes such as a color, brightness, and transition.

The list of profiles defined here serves a similar purpose to the [light_profiles.csv](https://www.home-assistant.io/integrations/light/#default-turn-on-values) file but it supports more color formats and the default turn-on profiles are configured in the `lights` element.

| Attribute         | Optional | Description |
| ----------------- | -------- |------------ |
| name              | no       | The name of the profile. Must be unique. |
| color_rgb         | yes      | RGB color as a list of 3 integers (red, green, blue) from 0 to 255. |
| color_rgbw        | yes      | RGBW color as a list of 4 integers (red, green, blue, white) from 0 to 255. |
| color_rgbww       | yes      | RGBWW color as a list of 5 integers (red, green, blue, cold white, warm white) from 0 to 255. |
| color_hs          | yes      | HS color as a list of 2 floats (hue, saturation), hue is scaled 0 to 360, saturation is scaled 0 to 100. |
| color_temp_kelvin | yes      | Color temperature in kelvin as an integer. |
| color_xy          | yes      | XY color as a list of 2 floats (x, y). This format cannot be represented as a favorite color. |
| color_white       | yes      | White color as an integer from 0 to 255. This format cannot be represented as a favorite color. |
| color_name        | yes      | A human-readable string of a color name, such as `blue` or `goldenrod`. All [CSS3 color names](https://www.w3.org/TR/css-color-3/#svg-color) are supported. This format cannot be represented as a favorite color. |
| brightness        | yes      | The default brightness as an integer from 0 to 255. |
| transition        | yes      | The default transition duration in seconds as a float. |

```yaml
  # Define some light profiles
  profiles:
    - name: Natural
      color_temp_kelvin: 4000
      brightness: 255
      transition: 0.5
    - name: Warm
      rgbw_color: [255, 195, 66, 255]
      brightness: 200
    - name: Red
      hs_color: [0, 100]
```

### Light configuration element

Each element of the `lights` list defines a light configuration.  A light configuration associates light profiles and favorite colors with a light.

The `light.turn_on` action applies the color, brightness, and transition attributes of the light's default profile by default unless overridden by the action's parameters.  The `light.turn_off` action applies the transition attribute of the light's default profile by default unless overridden by the action's parameters.

This element also configures the favorite colors that are shown in the light's more-info dialog to make it easier for users to pick relevant colors from the color attributes of each profile listed in `profiles` and additional `favorite_colors`.

| Attribute         | Optional | Description |
| ----------------- | -------- |------------ |
| entity_id         | no       | A entity ID or a list of entity IDs for the lights to be configured by this element. |
| profiles          | yes      | A list of light profile names.  The first entry in the list sets the default profile for the specified lights.  If the list of profiles is empty or absent, then the light does not have a default profile. |
| favorite_colors   | yes      | A list of additional favorite colors to include in the light's more-info dialog. |

```yaml
  # Define some light profiles
  lights:
    - entity_id: light.my_light_1
      profiles: [Natural, Warm]
    - entity_id: [light.my_light_2, light.my_light_3]
      profiles: [Warm, Red]
      favorite_colors:
        - ...
```

### Favorite colors element

Specifies a favorite color to be shown in a light's more-info dialog.  The frontend only supports a subset of all color formats as shown below.

| Attribute         | Optional | Description |
| ----------------- | -------- |------------ |
| color_rgb         | yes      | RGB color as a list of 3 integers (red, green, blue) from 0 to 255. |
| color_rgbw        | yes      | RGBW color as a list of 4 integers (red, green, blue, white) from 0 to 255. |
| color_rgbww       | yes      | RGBWW color as a list of 5 integers (red, green, blue, cold white, warm white) from 0 to 255. |
| color_hs          | yes      | HS color as a list of 2 floats (hue, saturation), hue is scaled 0 to 360, saturation is scaled 0 to 100. |
| color_temp_kelvin | yes      | Color temperature in kelvin as an integer. |

```yaml
  # A selection of favorite colors
  favorite_colors:
    - color_temp_kelvin: 4000
    - hs_color: [300, 70]
    - rgb_color: [255, 100, 100]
    - rgbw_color: [255, 100, 100, 50]
    - rgbww_color: [255, 100, 100, 50, 70]
```

## Actions

### light.turn_on

To apply a light profile to a light, use the [`light.turn_on` action](https://www.home-assistant.io/integrations/light/#action-lightturn_on) and specify the name of the profile.

```yaml
action: light.turn_on
data:
  entity_id: light.my_light
  profile: My Profile
```

If the action also specifies color, brightness, or transition attributes, then the corresponding attributes of the profile will be overridden by the values of the action, as in the following example.

```yaml
action: light.turn_on
data:
  entity_id: light.my_light
  profile: My Profile
  brightness: 40  # Overrides the brightness specified by the profile, if any
```

### scenery.get_favorite_colors

Gets the favorite colors of a light.

#### Request

```yaml
action: scenery.get_favorite_colors
data:
  entity_id: light.my_light
```

#### Response

If the entity's favorite colors have been set, returns a list of them.

```yaml
favorite_colors:
  - color_temp_kelvin: 4000
  - hs_color:
      - 300
      - 70
  - rgb_color:
      - 255
      - 100
      - 100
  - rgbw_color:
      - 255
      - 100
      - 100
      - 50
  - rgbww_color:
      - 255
      - 100
      - 100
      - 50
      - 70
```

If the entity's favorite colors have not been set, returns null.  In this case, the front-end generates default colors to show in the more-info dialog and it does not store them for the entity unless they are modified by the user.

```yaml
favorite_colors: null
```

If the entity's favorite colors have all been removed, returns an empty list.  In this case, the front-end will not show any favorite colors in the more-info dialog; it will not generate default colors as in the previous case.

```yaml
favorite_colors: []
```

### scenery.set_favorite_colors

Sets the favorite colors of a light.

```yaml
action: scenery.set_favorite_colors
data:
  entity_id: light.my_light
  favorite_colors:
    - color_temp_kelvin: 4000
    - hs_color: [300, 70]
    - rgb_color: [255, 100, 100]
    - rgbw_color: [255, 100, 100, 50]
    - rgbww_color: [255, 100, 100, 50, 70]
```

Omitting the `favorite_colors` attribute reverts the entity's favorite colors to the default.  In this case, the front-end generates default colors to show in the more-info dialog and it does not store them for the entity unless they are modified by the user.

```yaml
action: scenery.set_favorite_colors
data:
  entity_id: light.my_light
```

Setting the `favorite_colors` attribute to an empty list removes all of the favorite colors.  In this case, the front-end will not show any favorite colors in the more-info dialog; it will not generate default colors as in the previous case.

```yaml
action: scenery.set_favorite_colors
data:
  entity_id: light.my_light
  favorite_colors: []
```
