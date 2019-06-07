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

* Built-in LED as `light` component.
  > With brightness and colors.
* Built-in speaker and sounds library as `media_player` component.
  > With play, stop, mute, set_volume and play_media with ringtone ID as media ID.
* Built-in luminescence sensor (yes, there's one) as `sensor` component.
  > Sensor shows readings in lumens.
* Built-in alarm functionality as `alarm_control_panel` component.
  > Arm, disarm; night/home/away modes supported with alarm volumes: 5/15/70.

* Child sensors as `binary_sensor`.
  > Currently supported are: motion sensors, door/window sensors and button.

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
      class: motion    # motion sensor type
    - sid: lumi.0123
      class: opening   # door/window sensor
    - sid: lumi.ab01
      class: button    # button
```

## Zibgee devices

#### Pairing

You can pair new devices without entering Mi Home app by using HA service, just call:

```
miio_gateway.join_zigbee
```

service to enter pairing mode.

#### Adding sensor to HA

Once you've paired new device you'll be able to see "unregistered" sensor in your HA logs.

```
Received event from unregistered sensor: lumi.sensor_motion.v2 lumi.abcd - event.motion
                                         ^ model               ^ sid       ^ event that was sent
```

Use SID and model version to define it in `sensors:` section of `configuration.yaml`.

#### Using Zigbee button

Zigbee buttons are triggering an events for their actions.

Event type: `miio_gateway.button_action`

Available event data: `click_type`

Click type available payloads:
* `single_click`
* `double_click`
* `long_press`
* `long_release`

**Automation example:**

```yaml
- alias: 'Toggle the light'
  trigger:
    platform: event
    event_type: miio_gateway.button_action
    event_data:
      click_type: 'single_click'
  action:
    - service: light.toggle
      entity_id: light.my_light
```
