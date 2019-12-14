import logging

import homeassistant.components.alarm_control_panel as alarm

from . import DOMAIN, XiaomiGwDevice

from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY, STATE_ALARM_ARMED_HOME, STATE_ALARM_ARMED_NIGHT,
    STATE_ALARM_DISARMED, STATE_ALARM_TRIGGERED)

from homeassistant.components.alarm_control_panel.const import (
    SUPPORT_ALARM_ARM_AWAY, SUPPORT_ALARM_ARM_HOME,
    SUPPORT_ALARM_ARM_NIGHT, SUPPORT_ALARM_TRIGGER)

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up alarm")
    devices = []
    gateway = hass.data[DOMAIN]
    devices.append(XiaomiGatewayAlarm(gateway))
    add_entities(devices)

class XiaomiGatewayAlarm(XiaomiGwDevice, alarm.AlarmControlPanel):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "alarm_control_panel", None, "miio.gateway", "Gateway Alarm")

        # Default to ARMED_AWAY if no volume data was set
        self._state_by_volume = STATE_ALARM_ARMED_AWAY
        self._volume = 100
        # How to alarm
        self._ringtone = 1
        self._color = "ff0000"

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({ "method": "get_prop", "params": ["arming"] }, self._init_set_arming)
            self._send_to_hub({ "method": "get_prop", "params": ["alarming_volume"] }, self._init_set_volume)

    def _init_set_arming(self, result):
        if result is not None:
            _LOGGER.info("SETTING ARMED: " + str(result))
            if result == "on":
                self._state = self._state_by_volume
            elif result == "off":
                self._state = STATE_ALARM_DISARMED

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.info("SETTING ARMED VOL: " + str(result))
            self._volume = int(result)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        self._disarm()
        self._state = STATE_ALARM_DISARMED
        self.schedule_update_ha_state()

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._volume = 100
        self._arm()
        self._state = STATE_ALARM_ARMED_AWAY
        self.schedule_update_ha_state()

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._volume = 80
        self._arm()
        self._state = STATE_ALARM_ARMED_HOME
        self.schedule_update_ha_state()

    def alarm_arm_night(self, code=None):
        """Send arm night command."""
        self._volume = 90
        self._arm()
        self._state = STATE_ALARM_ARMED_NIGHT
        self.schedule_update_ha_state()

    def alarm_trigger(self, code=None):
        """Trigger the alarm."""
        self._siren()
        self._blink()
        self._state = STATE_ALARM_TRIGGERED
        self.schedule_update_ha_state()

    def _arm(self):
        self._send_to_hub({ "method": "set_alarming_volume", "params": [self._volume] })
        self._send_to_hub({ "method": "set_sound_playing", "params": ["off"] })
        self._send_to_hub({ "method": "set_arming", "params": ["on"] })

    def _disarm(self):
        self._send_to_hub({ "method": "set_sound_playing", "params": ["off"] })
        self._send_to_hub({ "method": "set_arming", "params": ["off"] })

    def _siren(self):
        # TODO playlist
        self._send_to_hub({ "method": "play_music_new", "params": [str(self._ringtone), self._volume] })

    def _blink(self):
        # TODO blink
        argbhex = [int("01" + self._color, 16), int("64" + self._color, 16)]
        self._send_to_hub({ "method": "set_rgb", "params": [argbhex[1]] })

    def _is_armed(self):
        if self._state is not None or self._state != STATE_ALARM_TRIGGERED or self._state != STATE_ALARM_DISARMED:
            return True
        return False

    def _get_state_by_volume(self, volume):
        if volume < 90:
            return STATE_ALARM_ARMED_HOME
        elif volume < 100:
            return STATE_ALARM_ARMED_NIGHT
        else:
            return STATE_ALARM_ARMED_AWAY

    @property
    def supported_features(self) -> int:
        return SUPPORT_ALARM_ARM_HOME | SUPPORT_ALARM_ARM_AWAY | SUPPORT_ALARM_ARM_NIGHT | SUPPORT_ALARM_TRIGGER

    @property
    def state(self):
        return self._state

    def parse_incoming_data(self, model, sid, event, params):

        arming = params.get("arming")
        if arming is not None:
            if arming == "on":
                self._state = self._get_state_by_volume(self._volume)
            elif arming == "off":
                self._state = STATE_ALARM_DISARMED
            return True

        alarming_volume = params.get("alarming_volume")
        if alarming_volume is not None:
            self._volume = int(alarming_volume)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume
                return True

        return False
