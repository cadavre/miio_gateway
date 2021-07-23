[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

# Miio Gateway

This is Miio Gateway EU version implementation based on encyryption-less `miio_client` 
developed by [@roth-m](https://github.com/roth-m).

This component includes `XiaomiGw` class to communicate with Xiaomi devices via UDP port 54321.

## What for?

In general it allows (modified) devices like `lumi.gateway.mieu01` to be controlled via LAN **instead of Xiaomi Cloud**.

Once you replace original `miio_client` with modified one – you won't be able to control gateway via Mi Home app.
But... why you would? ;)

## Requirements

**Based on `lumi.gateway.mieu01`**

For mentioned gateway you need to gain access to SSH and **add** another `miio_client` binary to device.

1. To obtain SSH access follow [this tutorial](https://community.openhab.org/t/solved-openhab2-xiaomi-mi-gateway-does-not-respond/52963/188?u=cadavre).
2. Add new binary following [this readme](https://github.com/roth-m/miioclient-mqtt/tree/master/miio_client).

**Notice!** You need to keep old `miio_client` because it's required for initialization of connection.

**Warning!** Launching modified `miio_client` will disable Xiaomi cloud access so you won't be able to
control the gateway from Mi Home app!

## What is supported

* Built-in LED as `light.miio_gateway` component.
  > With brightness and colors.
* Built-in speaker and sounds library as `media_player.miio_gateway` component.
  > With play, stop, mute, set_volume and play_media with ringtone ID as media ID.
* Built-in luminescence sensor (yes, there's one) as `sensor.miio_gateway_illuminance` component.
  > Sensor shows readings in lumens.
* Built-in alarm functionality as `alarm_control_panel.miio_gateway` component.
  > Arm, disarm; night/home/away modes supported via alarm volumes: 5/15/70.

* Child sensors as `binary_sensor`.
  > Currently supported are:
  > * motion sensors,
  > * door/window sensors,
  > * leak sensors,
  > * smoke sensors,
  > * buttons.

* Child sensors as `sensor`.
  > Currently supported are:
  > * temperature sensors,
  > * humidity sensors,
  > * pressure sensors.

## Installation of HA component

1. Clone this repo as `miio_gateway` dir into `$HA_CONFIG_DIR/custom_components/`:
   ```
   $ cd custom_components
   $ git clone git@github.com:cadavre/miio_gateway.git ./miio_gateway
   ```
2. Setup `$HA_CONFIG_DIR/configuration.yaml`:

```yaml
miio_gateway:
  host: 192.168.1.2    # IP of your gateway
  port: 54321          # port running miio_client, defaults to 54321
  sensors:             # sensors that will be available in HA (optional)
    - sid: lumi.abcd
      class: motion                           # motion sensor
      friendly_name: My garage motion sensor  # display name (optional)
    - sid: lumi.0123
      class: door                             # door sensor
      restore: true                           # will restore sensor state after HA reboot
    - sid: lumi.ab01
      class: button                           # button
    - sid: lumi.smk1
      class: smoke                            # smoke sensor
```

## Zibgee devices

### Pairing

You can pair new devices without entering Mi Home app by using HA service, just call:

```
miio_gateway.join_zigbee
```

service to enter pairing mode. No need to kep original `miio_client` up for 10mins after gateway reboot!

### Adding sensor to HA

Once you've paired new device you'll be able to see "unregistered" sensor in your HA logs.

```
Received event from unregistered sensor: lumi.sensor_motion.v2 lumi.abcd - event.motion
                                         ^ model               ^ sid       ^ event that was sent
```

Use SID to define it in `sensors:` section of `configuration.yaml`.

### Using Zigbee button

Zigbee buttons are triggering an events for their actions.

Event type: `miio_gateway.action`

Available event data: `event_type`

Click type available payloads:
* `click`
* `double_click`
* `long_click_press`
* `long_click_release`

**Automation example:**

```yaml
- alias: 'Toggle the light'
  trigger:
    platform: event
    event_type: miio_gateway.action
    event_data:
      event_type: 'click'
      entity_id: 'binary_sensor.lumi_ab01_button'
  action:
    - service: light.toggle
      entity_id: light.my_light
```

### Using vibration sensor

Just like `button` – vibration sensor sends one of two events:
* `vibration` on vibration
* `free_fall` on free-fall
* `tilt` on tilt by an angle
* `bed_activity` on... bed activity? :D

You can use them just like with buttons. Event type is still `event_type: miio_gateway.action`.

## Alarm finetuning

Since implementation of HASS'es `alarm_control_panel` into `miio_gateway` component
requires a lot of copy-paste – I abandoned this idea.

Instead you can use [coupled_alarms](https://github.com/cadavre/coupled_alarms).

## Changelog

### v1.5.0
* HACS custom repository

### v1.4.0
* Added `manifest.json` in order to be compatible with HA 2021.06 and updating some entities based on the HA entities deprecation.  

### v1.3.1

* Updated `alarm_control_panel` with `supported_features` to work with HA 0.103 and above.

### v1.3

* Added `restore` param to sensor mapping. Defaults to `false`, will restore pre-HA restart state if set to `true`.

### v1.2

* UDP socket gateway connection rebuilt.
* Supports re-connections now.
* Gateway after-unavailable state is now refreshed.
* Fixed wrong logging params that caused gateway to freeze, thanks @quarcko !

### v1.1

* Changed entity_id and name generation methods.
* Added support for temp/humid/pressure sensors.
* Added support for vibration sensor.
* Added `friendly_name` to sensor definition in config.yml.
* Sensor `class` can be now anything from binary_sensor (door, garage_door, window, motion, moving, opening, smoke, vibration and more).
  Keep in mind that not all Miio events are supported yet! Listed above are supported.
* Sensor `class` can be now anything from sensor (humidity, illuminance, temperature, pressure and more).
  Keep in mind that not all Miio events are supported yet! Listed above are supported.

#### Breaking changes (v1.1)

##### General

* Due to changed method of entity_id generation, after update, all entities will have new entity_ids.
  You can remove old entities before update via `Settings -> Entity registry` with `miio_gateway` tag.
  Then you can update this component and restart HA.
  After restart new entities will be visible – you'll be able to change its entity_id via "Entity registry" too.

##### Button events

* `miio_gateway.button_action` event changed to `miio_gateway.action`
* `click_type` event param changed to `event_type`
* `single_click` changed to `click`
* `long_press` changed to `long_click_press`
* `long_release` changed to `long_click_release`

## Not supported yet

Not supported but **likely to work** with:

* Occupancy detectors.
* Plug switches.
* Locks.
* Smart Cubes.
* Remotes(?).
* Relays(?).
* Curtains(?).
