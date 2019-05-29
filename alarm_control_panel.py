import logging

import homeassistant.components.alarm_control_panel as alarm

from . import DOMAIN, XiaomiGwDevice

from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY, STATE_ALARM_ARMED_HOME, STATE_ALARM_ARMED_NIGHT,
    STATE_ALARM_DISARMED, STATE_ALARM_TRIGGERED)

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up alarm")
    devices = []
    gateway = hass.data[DOMAIN]
    devices.append(XiaomiGatewayAlarm("gw_alarm", gateway))
    add_entities(devices)

class XiaomiGatewayAlarm(XiaomiGwDevice, alarm.AlarmControlPanel):

    def __init__(self, name, gw):
        self._state = None
        # TODO init with "get_arming"
        XiaomiGwDevice.__init__(self, name, gw)

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        _LOGGER.debug("alarm_disarm")
        self._send_to_hub({ "method": "set_arming", "params": ["off"] })
        self._state = STATE_ALARM_DISARMED

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        _LOGGER.debug("alarm_arm_away")
        self._send_to_hub({ "method": "set_arming", "params": ["on"] })
        self._state = STATE_ALARM_ARMED_AWAY

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        _LOGGER.debug("alarm_arm_home")
        self._send_to_hub({ "method": "set_arming", "params": ["on"] })
        self._state = STATE_ALARM_ARMED_HOME

    def alarm_arm_night(self, code=None):
        """Send arm night command."""
        _LOGGER.debug("alarm_arm_night")
        self._send_to_hub({ "method": "set_arming", "params": ["on"] })
        self._state = STATE_ALARM_ARMED_NIGHT

    @property
    def state(self):
        return self._state

    def parse_incoming_data(self, params):
        if params is None:
            return False

        # TODO listen for arming changes

        return False
