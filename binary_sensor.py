import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDevice, DEVICE_CLASSES)

from homeassistant.helpers.event import async_track_point_in_utc_time
import homeassistant.util.dt as dt_util

from . import DOMAIN, CONF_DATA_DOMAIN, CONF_SENSOR_SID, CONF_SENSOR_CLASS, CONF_SENSOR_NAME, EVENT_VALUES, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

ATTR_LAST_ACTION = "last_action"

# Door Window Opening Sensor
EVENT_OPEN = "event.open"
EVENT_CLOSE = "event.close"
EVENT_NO_CLOSE = "event.no_close"

# Motion Sensor
EVENT_MOTION = "event.motion"
EVENT_NO_MOTION = "event.no_motion"

# Leak Sensor
EVENT_LEAK = "event.leak"
EVENT_NO_LEAK = "event.no_leak"

# Vibration Sensor
EVENT_VIBRATION = "event.vibrate"
EVENT_BED_ACTIVITY = "event.bed_activity"
EVENT_FREEFALL = "event.free_fall"
EVENT_TILT = "event.tilt"
EVENT_TILT_ANGLE = "event.final_tilt_angle"
EVENT_COORDINATION = "event.coordination"

# Button Sensor
DEVICE_CLASS_BUTTON = "button"
EVENT_SINGLE_CLICK = "event.click"
EVENT_DOUBLE_CLICK = "event.double_click"
EVENT_LONG_PRESS = "event.long_click_press"
EVENT_LONG_RELEASE = "event.long_click_release"

IGNORED_EVENTS = [EVENT_VALUES, EVENT_TILT_ANGLE, EVENT_COORDINATION]

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up binary sensors")

    # Make a list of all default + custom device classes
    all_device_classes = DEVICE_CLASSES
    all_device_classes.append(DEVICE_CLASS_BUTTON)

    gateway = hass.data[DOMAIN]
    entities = []

    for cfg in hass.data[CONF_DATA_DOMAIN]:
        if not cfg:
            cfg = {}

        sid = cfg.get(CONF_SENSOR_SID)
        device_class = cfg.get(CONF_SENSOR_CLASS)
        name = cfg.get(CONF_SENSOR_NAME)

        if sid is None or device_class is None:
            continue

        gateway.known_sids.append(sid)

        if device_class in all_device_classes:
            _LOGGER.info("Registering " + str(device_class) + " sid " + str(sid) + " as binary_sensor")
            entities.append(XiaomiGwBinarySensor(gateway, device_class, sid, name))

    if not entities:
        _LOGGER.info("No binary_sensors configured")
        return False

    add_entities(entities)
    return True

class XiaomiGwBinarySensor(XiaomiGwDevice, BinarySensorDevice):

    def __init__(self, gw, device_class, sid, name):
        XiaomiGwDevice.__init__(self, gw, "binary_sensor", device_class, sid, name)

        # Custom Button device class
        if device_class == DEVICE_CLASS_BUTTON:
            self._device_class = None
        else:
            self._device_class = device_class

        self._last_action = None

        self._state_timer = None

    @property
    def is_on(self):
        return self._state

    @property
    def device_class(self):
        return self._device_class

    @property
    def device_state_attributes(self):
        attrs = super().device_state_attributes
        if self._last_action is not None:
            attrs.update({ATTR_LAST_ACTION: self._last_action})
        return attrs

    def parse_incoming_data(self, model, sid, event, params):

        # Ignore params event for this platform
        if event in IGNORED_EVENTS:
            return False

        if event in [EVENT_OPEN, EVENT_NO_CLOSE, EVENT_MOTION, EVENT_LEAK]:
            self._state = True
        elif event in [EVENT_CLOSE, EVENT_NO_MOTION, EVENT_NO_LEAK]:
            self._state = False
        else:
            event_type = event.split(".")[1]
            self._gw.hass.bus.fire('miio_gateway.action', {
                'entity_id': self.entity_id,
                'event_type': event_type
            })
            self._state = event_type
            self._last_action = event_type
            self._start_state_timer()

        return True

    def _start_state_timer(self):
        """Start state timer."""
        if self._state_timer is not None:
            self._state_timer()
            self._state_timer = None
        restore_state_time = dt_util.utcnow() + timedelta(seconds=15)
        self._state_timer = async_track_point_in_utc_time(self.hass, self._stop_state_timer, restore_state_time)

    def _stop_state_timer(self, time):
        """Stop state timer."""
        self._state_timer = None
        self._state = False
        self.schedule_update_ha_state()
