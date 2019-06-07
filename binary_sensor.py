import logging

from homeassistant.components.binary_sensor import BinarySensorDevice
from homeassistant.util.dt import utcnow

from . import DOMAIN, CONF_DATA_DOMAIN, CONF_SENSOR_SID, CONF_SENSOR_CLASS, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

# Generic Sensor
EVENT_LOG = "_otc.log"
EVENT_KEEPALIVE = "event.keepalive"
ATTR_ALIVE = "Heartbeat"
ATTR_VOLTAGE = "Voltage"
ATTR_LQI = "Link quality"
ATTR_MODEL = "Model"

# Door Window Opening Sensor
DEVICE_CLASS_OPENING = "opening"
EVENT_OPEN = "event.open"
EVENT_CLOSE = "event.close"
EVENT_NO_CLOSE = "event.no_close"

# Motion Sensor
DEVICE_CLASS_MOTION = "motion"
EVENT_MOTION = "event.motion"
EVENT_NO_MOTION = "event.no_motion"

# Button Sensor
DEVICE_CLASS_BUTTON = "button"
ATTR_LAST_ACTION = "Last action"
EVENT_SINGLE_CLICK = "event.click"
EVENT_DOUBLE_CLICK = "event.double_click"
EVENT_LONG_PRESS = "event.long_click_press"
EVENT_LONG_RELEASE = "event.long_click_release"

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up binary sensors")

    gateway = hass.data[DOMAIN]

    entities = []

    for cfg in hass.data[CONF_DATA_DOMAIN]:
        if not cfg:
            cfg = {}

        sid = cfg.get(CONF_SENSOR_SID)
        device_class = cfg.get(CONF_SENSOR_CLASS)

        if sid is None or device_class is None:
            continue

        _LOGGER.info("Registering " + str(device_class) + " sid " + str(sid))

        gateway.known_sids.append(sid)

        if device_class == DEVICE_CLASS_OPENING:
            entities.append(XiaomiGwDoorSensor(sid, gateway))
        elif device_class == DEVICE_CLASS_MOTION:
            entities.append(XiaomiGwMotionSensor(sid, gateway))
        elif device_class == DEVICE_CLASS_BUTTON:
            entities.append(XiaomiGwButton(sid, gateway))
        else:
            _LOGGER.info("Unrecognized device class " + str(device_class))

    if not entities:
        _LOGGER.info("No sensors configured")
        return False

    add_entities(entities)
    return True

class XiaomiGwBinarySensor(XiaomiGwDevice, BinarySensorDevice):

    def __init__(self, name, sid, gw, device_class):
        XiaomiGwDevice.__init__(self, name, gw)
        if device_class == DEVICE_CLASS_BUTTON:
            self._device_class = None
        else:
            self._device_class = device_class
        self._sid = sid
        self._unique_id = "{}_{}".format(self._sid, self._device_class)
        self._name = name
        self._alive = None
        self._voltage = None
        self._lqi = None
        self._model = None

    @property
    def should_poll(self):
        return False

    @property
    def is_on(self):
        return self._state

    @property
    def device_class(self):
        return self._device_class

    @property
    def device_state_attributes(self):
        attrs = { ATTR_VOLTAGE: self._voltage, ATTR_LQI: self._lqi, ATTR_MODEL: self._model, ATTR_ALIVE: self._alive }
        attrs.update(super().device_state_attributes)
        return attrs

    def preparse_data(self, params, event, model, sid):
        if event is None or sid is None:
            return False

        if self._sid != sid:
            return False

        if model is not None:
            self._model = model

        if event == EVENT_KEEPALIVE:
            self._alive = utcnow()
            return True

        if event == EVENT_LOG:
            zigbeeData = params.get("subdev_zigbee")
            if zigbeeData is not None:
                self._voltage = zigbeeData.get("voltage")
                self._lqi = zigbeeData.get("lqi")
                _LOGGER.info("Vol:" + str(self._voltage) + " lqi:" + str(self._lqi))
                return True
            return False

        return None

class XiaomiGwDoorSensor(XiaomiGwBinarySensor):

    def __init__(self, sid, gw):
        XiaomiGwBinarySensor.__init__(self, "Opening Sensor", sid, gw, DEVICE_CLASS_OPENING)

    def parse_incoming_data(self, params, event, model, sid):
        preparse = self.preparse_data(params, event, model, sid)
        if preparse is not None:
            return preparse

        if event == EVENT_CLOSE:
            self._state = False
        elif event == EVENT_OPEN:
            self._state = True
        elif event == EVENT_NO_CLOSE:
            self._state = True

        return True

class XiaomiGwMotionSensor(XiaomiGwBinarySensor):

    def __init__(self, sid, gw):
        XiaomiGwBinarySensor.__init__(self, "Motion Sensor", sid, gw, DEVICE_CLASS_MOTION)

    def parse_incoming_data(self, params, event, model, sid):
        preparse = self.preparse_data(params, event, model, sid)
        if preparse is not None:
            return preparse

        if event == EVENT_MOTION:
            self._state = True
        elif event == EVENT_NO_MOTION:
            self._state = False

        return True

class XiaomiGwButton(XiaomiGwBinarySensor):

    def __init__(self, sid, gw):
        XiaomiGwBinarySensor.__init__(self, "Zigbee Button", sid, gw, DEVICE_CLASS_BUTTON)
        self._state = True
        self._last_action = None

    @property
    def device_state_attributes(self):
        attrs = {ATTR_LAST_ACTION: self._last_action}
        attrs.update(super().device_state_attributes)
        return attrs

    def parse_incoming_data(self, params, event, model, sid):
        preparse = self.preparse_data(params, event, model, sid)
        if preparse is not None:
            return preparse

        if event == EVENT_SINGLE_CLICK:
            click_type = "single_click"
        elif event == EVENT_DOUBLE_CLICK:
            click_type = "double_click"
        elif event == EVENT_LONG_PRESS:
            click_type = "long_press"
        elif event == EVENT_LONG_RELEASE:
            click_type = "long_release"

        self._gw.hass.bus.fire('miio_gateway.button_action', {
            'entity_id': self._unique_id,
            'click_type': click_type
        })

        self._last_action = click_type

        return True
