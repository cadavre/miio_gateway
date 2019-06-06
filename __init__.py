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

TIME_INTERVAL_PING = timedelta(minutes=5)
TIME_INTERVAL_UNAVAILABLE = timedelta(minutes=11)

DOMAIN = "miio_gateway"
CONF_DATA_DOMAIN = "miio_gateway_config"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SENSORS = "sensors"
CONF_SENSOR_SID = "sid"
CONF_SENSOR_CLASS = "class"

SENSORS_CONFIG_SCHEMA = vol.Schema({
    vol.Optional(CONF_SENSOR_SID): cv.string,
    vol.Optional(CONF_SENSOR_CLASS): cv.string,
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
                data = self._miio_msg_decode(data)

                self._set_available()

                # Skip all `internal.` methods.
                method = data.get("method")
                if method is not None and method.startswith("internal."):
                    continue

                # Call result callback if registered for given miio_id.
                miio_id = data.get("id")
                if miio_id is not None and miio_id in self._result_callbacks:
                    result = data.get("result")
                    if isinstance(result, list):
                        result = result[0]
                    self._result_callbacks[miio_id](result)

                # If params obj is in arr – unwrap it.
                params = data.get("params")
                if isinstance(params, list):
                    if len(params) == 0:
                        params = None
                    else:
                        params = params[0]

                # We're not interested in int params right now.
                if isinstance(params, int):
                    params = None

                # Received binary_sensor event.
                sid = None
                model = None
                event = None
                if method is not None and method.find("event.") != -1:
                    sid = data.get("sid")
                    model = data.get("model")
                    event = method
                    if sid is not None and model is not None:
                        self._event_received(sid, model, event)

                for func in self.callbacks:
                    func(params, event, model, sid)
            except socket.timeout:
                pass

    def _event_received(self, sid, model, event):
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
        if data[-1] == 0:
            data = data[:-1]
        res = {}
        try:
            fixed_str = data.decode().replace('}{', '},{')
            res = json.loads(fixed_str)
        except:
            print("Bad JSON received")
        return res

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
                func({"availability": True})

    @callback
    def _async_set_unavailable(self, now):
        """Set state to UNAVAILABLE."""
        if self._ping_loss > 2:
            _LOGGER.info("Gateway became unavailable by timeout!")
            self._is_available = False
            for func in self.callbacks:
                func({"availability": False})

    @callback
    def _async_send_ping(self, now):
        """Send ping to gateway."""
        self._ping_loss = self._ping_loss + 1
        self.send_to_hub({"method": "internal.PING"})

class XiaomiGwDevice(Entity):
    """A generic device of Gateway."""

    def __init__(self, device_type, gw):
        """Initialize the device."""
        self._state = None
        self._type = device_type
        self._unique_id = "{}_{}".format(gw.unique_id(), self._type)
        self._name = self._unique_id

        self._gw = gw
        self._send_to_hub = self._gw.send_to_hub

        self._device_state_attributes = {}

    async def async_added_to_hass(self):
        """Add push data listener for this device."""
        self._gw.callbacks.append(self._add_push_data_job)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def available(self):
        """Return True if entity is available."""
        return self._gw._is_available

    @property
    def should_poll(self):
        """Return the polling state. No polling needed."""
        return False

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return self._device_state_attributes

    def _add_push_data_job(self, *args):
        self.hass.add_job(self.push_data, *args)

    @callback
    def push_data(self, params, event, model, sid):
        """Push data that came from gateway to parser. Update HA state if any changes were made."""
        if isinstance(params, str):
            return
        has_availability = False
        if params is not None and params.get("availability") is not None:
            has_availability = True
        has_data = self.parse_incoming_data(params, event, model, sid)
        if has_availability or has_data:
            self.async_schedule_update_ha_state()

    def parse_incoming_data(self, params, event, model, sid):
        """Parse incoming data from gateway. Abstract."""
        raise NotImplementedError()
