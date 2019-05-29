import logging

from homeassistant.const import (DEVICE_CLASS_ILLUMINANCE)

from . import DOMAIN, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    "illumination": ["lm", "mdi:white-balance-sunny", DEVICE_CLASS_ILLUMINANCE]
}

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up illumination sensor")
    devices = []
    gateway = hass.data[DOMAIN]
    devices.append(XiaomiGatewaySensor("gw_illumination", "illumination", gateway))
    add_entities(devices)

class XiaomiGatewaySensor(XiaomiGwDevice):

    def __init__(self, name, type, gw):
        self._sensor_type = type
        self._state = None
        XiaomiGwDevice.__init__(self, name, gw)

    @property
    def icon(self):
        try:
            return SENSOR_TYPES.get(self._sensor_type)[1]
        except TypeError:
            return None

    @property
    def unit_of_measurement(self):
        try:
            return SENSOR_TYPES.get(self._sensor_type)[0]
        except TypeError:
            return None

    @property
    def device_class(self):
        try:
            return SENSOR_TYPES.get(self._sensor_type)[2]
        except TypeError:
            return None

    @property
    def state(self):
        return self._state

    def parse_incoming_data(self, params):
        if params is None:
            return False

        illumination = params.get("illumination")
        if illumination is not None:
            self._state = illumination
            return True

        return False
