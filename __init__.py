import logging
import socket
import json
from threading import Thread
from multiprocessing import Queue
from datetime import timedelta

import voluptuous as vol

from homeassistant.const import (
    CONF_HOST, CONF_MAC, CONF_PORT,
    EVENT_HOMEASSISTANT_STOP)

from homeassistant.core import callback
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.dt import utcnow

_LOGGER = logging.getLogger(__name__)

TIME_INTERVAL_PING = timedelta(minutes=1)
TIME_INTERVAL_UNAVAILABLE = timedelta(minutes=3)

DOMAIN = "miio_gateway"
CONF_DATA_DOMAIN = "miio_gateway_config"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SENSORS = "sensors"
CONF_SENSOR_SID = "sid"
CONF_SENSOR_CLASS = "class"
CONF_SENSOR_NAME = "friendly_name"

ATTR_ALIVE = "heartbeat"
ATTR_VOLTAGE = "voltage"
ATTR_LQI = "link_quality"
ATTR_MODEL = "model"

EVENT_METADATA = "internal.metadata"
EVENT_VALUES = "internal.values"
EVENT_KEEPALIVE = "event.keepalive"
EVENT_AVAILABILITY = "event.availability"

SENSORS_CONFIG_SCHEMA = vol.Schema({
    vol.Optional(CONF_SENSOR_SID): cv.string,
    vol.Optional(CONF_SENSOR_CLASS): cv.string,
    vol.Optional(CONF_SENSOR_NAME): cv.string,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=54321): cv.port,
        vol.Optional(CONF_SENSORS, default={}): vol.Any(cv.ensure_list, [SENSORS_CONFIG_SCHEMA]),
    })
}, extra=vol.ALLOW_EXTRA)

SERVICE_JOIN_ZIGBEE = "join_zigbee"
SERVICE_SCHEMA = vol.Schema({})

def setup(hass, config):
    """Setup gateway from config."""
    _LOGGER.info("Starting setup...")

    gateway = XiaomiGw(hass, config[DOMAIN][CONF_HOST], config[DOMAIN][CONF_PORT])
    hass.data[DOMAIN] = gateway
    hass.data[CONF_DATA_DOMAIN] = config[DOMAIN].get(CONF_SENSORS)

    gateway.create_socket()
    gateway.listen_socket()
    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, gateway.close_socket)

    for component in ["light", "media_player", "binary_sensor", "sensor", "alarm_control_panel"]:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

    def join_zigbee_service_handler(service):
        gateway = hass.data[DOMAIN]
        gateway.send_to_hub({ "method": "start_zigbee_join" })

    hass.services.register(
        DOMAIN, SERVICE_JOIN_ZIGBEE, join_zigbee_service_handler,
        schema=SERVICE_SCHEMA)

    return True

class XiaomiGw:
    """Gateway representation with socket to connect to it."""

    def __init__(self, hass, host, port):
        """Initialize the gateway."""
        self.hass = hass

        self._host = host
        self._port = port

        self._socket = None
        self._thread = None
        self._listening = False

        self._send_queue = Queue(maxsize=25)
        self._miio_id = 0

        self.callbacks = []
        self._result_callbacks = {}

        self._is_available = True
        self._unavailability_listener = None
        self._ping_listener = None
        self._ping_loss = 0

        self.known_sids = []
        self.known_sids.append("miio.gateway")

        import hashlib, base64
        self._unique_id = base64.urlsafe_b64encode(hashlib.sha1((self._host + ":" + str(self._port)).encode("utf-8")).digest())[:10].decode("utf-8")

    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    def create_socket(self):
        """Create connection socket."""
        _LOGGER.info("Creating socket...")
        self._socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self._socket.settimeout(0.1)

    def close_socket(self, event):
        """Close connection socket."""
        self._listening = False
        if self._socket is not None:
            _LOGGER.info("Closing socket...")
            self._socket.close()
            self._socket = None
        self._thread.join()

    def listen_socket(self):
        """Listen for messages from gateway."""
        self._listening = True
        self._thread = Thread(target=self._run_socket_thread, args=())
        self._thread.daemon = True
        self._thread.start()
        self._async_track_availability()

    def send_to_hub(self, data, callback=None):
        """Send data to hub."""
        miio_id, data = self._miio_msg_encode(data)
        if callback is not None:
            _LOGGER.info("Adding callback for call ID: " + str(miio_id))
            self._result_callbacks[miio_id] = callback
        self._send_queue.put(data)


    def _run_socket_thread(self):
        _LOGGER.info("Starting listener thread...")
        while self._listening:

            if self._socket is None:
                continue

            while not self._send_queue.empty():
                data = self._send_queue.get();
                _LOGGER.debug("Sending data:")
                _LOGGER.debug(data)
                self._socket.sendto(data, (self._host, self._port))

            try:
                data = self._socket.recvfrom(1480)[0]
                _LOGGER.debug("Received data:")
                _LOGGER.debug(data)

                # Gateway available = true
                self._set_available()

                # Get all messages from response data
                resps = self._miio_msg_decode(data)

                # Parse all messages in response
                self._parse_received_resps(resps)

            except socket.timeout:
                pass


    def _parse_received_resps(self, resps):
        """Parse received data."""
        for res in resps:

            if "result" in res:
                """Handling request result."""

                miio_id = res.get("id")
                if miio_id is not None and miio_id in self._result_callbacks:

                    result = res.get("result")
                    # Convert '{"result":["ok"]}' to single value "ok"
                    if isinstance(result, list):
                        # Parse '[]' result
                        if len(result) == 0:
                            result = "unknown"
                        else:
                            result = result[0]
                    self._result_callbacks[miio_id](result)

            elif "method" in res:
                """Handling received data."""

                if "model" not in res:
                    res["model"] = "lumi.gateway.mieu01"
                model = res.get("model")

                if "sid" not in res:
                    res["sid"] = "miio.gateway"
                sid = res.get("sid")

                params = res.get("params")
                if params is None:
                    # Ensure params is dict
                    params = {}
                if isinstance(params, list):
                    # Parse '[]' params
                    if len(params) == 0:
                        # Convert empty list to empty dict
                        params = {}
                    else:
                        # Extract list to dict
                        params = params[0]
                    if not isinstance(params, dict):
                        params = {"data": params}

                method = res.get("method")
                if method.startswith("internal."):
                    """Internal method, nothing to do here."""
                    continue
                elif method in ["_sync.neighborDevInfo"]:
                    """Known but non-handled method."""
                    continue
                elif method.startswith("event."):
                    """Received event."""
                    event = method
                    self._event_received(model, sid, event)
                elif method == "_otc.log":
                    """Received metadata."""
                    event = EVENT_METADATA
                elif method == "props":
                    """Received values."""
                    event = EVENT_VALUES
                else:
                    """Unknown method."""
                    print("Received unknown method: " + str(data))
                    continue

                # Now we have all the data we need
                for func in self.callbacks:
                    func(model, sid, event, params)

            else:
                """Nothing that we can handle."""
                print("Non-parseable data: " + str(data))

    def _event_received(self, model, sid, event):
        """Callback for receiving sensor event from gateway."""
        _LOGGER.debug("Received event: " + str(model) + " " + str(sid) + " - " + str(event))
        if sid not in self.known_sids:
            _LOGGER.info("Received event from unregistered sensor: " + str(model) + " " + str(sid) + " - " + str(event))


    def _miio_msg_encode(self, data):
        """Encode data to be sent to gateway."""
        if data.get("method") and data.get("method") == "internal.PING":
            msg = data
        else:
            if self._miio_id != 12345:
                self._miio_id = self._miio_id + 1
            else:
                self._miio_id = self._miio_id + 2
            if self._miio_id > 999999999:
                self._miio_id = 1
            msg = { "id": self._miio_id };
            msg.update(data);
        return([self._miio_id, (json.dumps(msg)).encode()])

    def _miio_msg_decode(self, data):
        """Decode data received from gateway."""

        # Trim `0` from the end of data string
        if data[-1] == 0:
            data = data[:-1]

        # Prepare array of responses
        resps = []
        try:
            data_arr = "[" + data.decode().replace("}{", "},{") + "]"
            resps = json.loads(data_arr)
        except:
            print("Bad JSON received: " + str(data))
        return resps


    def _async_track_availability(self):
        """Set tracker to ping and check availability."""
        _LOGGER.debug("Starting to track availability...")
        self._unavailability_listener = async_track_time_interval(
            self.hass, self._async_set_unavailable,
            TIME_INTERVAL_UNAVAILABLE)
        self._ping_listener = async_track_time_interval(
            self.hass, self._async_send_ping,
            TIME_INTERVAL_PING)

    def _set_available(self):
        """Set state to AVAILABLE."""
        was_unavailable = not self._is_available
        self._is_available = True
        self._ping_loss = 0
        if was_unavailable:
            _LOGGER.info("Gateway became available!")
            for func in self.callbacks:
                func(None, None, EVENT_AVAILABILITY)

    @callback
    def _async_set_unavailable(self, now):
        """Set state to UNAVAILABLE."""
        if self._ping_loss > 2:
            _LOGGER.info("Gateway became unavailable by timeout!")
            self._is_available = False
            for func in self.callbacks:
                func(None, None, EVENT_AVAILABILITY)

    @callback
    def _async_send_ping(self, now):
        """Send ping to gateway."""
        self._ping_loss = self._ping_loss + 1
        self.send_to_hub({"method": "internal.PING"})


class XiaomiGwDevice(Entity):
    """A generic device of Gateway."""

    def __init__(self, gw, platform, device_class = None, sid = None, name = None):
        """Initialize the device."""

        self._state = None
        self._sid = sid
        self._name = name

        self._model = None
        self._voltage = None
        self._lqi = None
        self._alive = None

        if device_class is None:
            self._unique_id = "{}_{}".format(sid, platform)
            self.entity_id = platform + "." + sid.replace(".", "_")
        else:
            self._unique_id = "{}_{}_{}".format(sid, platform, device_class)
            self.entity_id = platform + "." + sid.replace(".", "_") + "_" + device_class

        self._gw = gw
        self._send_to_hub = self._gw.send_to_hub

    async def async_added_to_hass(self):
        """Add push data listener for this device."""
        self._gw.callbacks.append(self._add_push_data_job)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self):
        return self._gw._is_available

    @property
    def should_poll(self):
        return False

    @property
    def device_state_attributes(self):
        attrs = { ATTR_VOLTAGE: self._voltage, ATTR_LQI: self._lqi, ATTR_MODEL: self._model, ATTR_ALIVE: self._alive }
        return attrs

    def _add_push_data_job(self, *args):
        self.hass.add_job(self._push_data, *args)

    @callback
    def _push_data(self, model = None, sid = None, event = None, params = {}):
        """Push data that came from gateway to parser. Update HA state if any changes were made."""

        # If should/need get into real parsing
        init_parse = self._pre_parse_data(model, sid, event, params)
        if init_parse is not None:
            # Update HA state
            if init_parse == True:
                self.async_schedule_update_ha_state()
            return

        # If parsed some data
        has_data = self.parse_incoming_data(model, sid, event, params)
        if has_data:
            # Update HA state
            self.async_schedule_update_ha_state()
            return

    def parse_incoming_data(self, model, sid, event, params):
        """Parse incoming data from gateway. Abstract."""
        raise NotImplementedError()

    def _pre_parse_data(self, model, sid, event, params):
        """Make initial checks and return bool if parsing shall be ended."""

        # Generic handler for availability change
        # Devices are getting availability state from Gateway itself
        if event == EVENT_AVAILABILITY:
            return True

        if self._sid != sid:
            return False

        if model is not None:
            self._model = model

        # Generic handler for event.keepalive
        if event == EVENT_KEEPALIVE:
            self._alive = utcnow()
            return True

        # Generic handler for _otg.log
        if event == EVENT_METADATA:
            zigbeeData = params.get("subdev_zigbee")
            if zigbeeData is not None:
                self._voltage = zigbeeData.get("voltage")
                self._lqi = zigbeeData.get("lqi")
                _LOGGER.info("Vol:" + str(self._voltage) + " lqi:" + str(self._lqi))
                return True
            return False

        return None
