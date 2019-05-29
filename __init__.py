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

CONF_HOST = "host"
CONF_PORT = "port"

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=54321): cv.port,
    })
}, extra=vol.ALLOW_EXTRA)

def setup(hass, config):
    """Setup gateway from config."""
    _LOGGER.info("Starting setup...")

    gateway = XiaomiGw(hass, config[DOMAIN][CONF_HOST], config[DOMAIN][CONF_PORT])
    hass.data[DOMAIN] = gateway

    gateway.create_socket()
    gateway.listen()
    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, gateway.close_socket)

    for component in ["light", "media_player", "sensor", "alarm_control_panel"]:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

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
        self._ping_callback = None

        self._is_available = True
        self._unavailability_listener = None
        self._ping_listener = None

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

    def send_to_hub(self, data):
        """Send data to hub."""
        #_LOGGER.debug("Sending data:")
        #_LOGGER.debug(data)
        #raw = self._miio_msg_encode(data)
        #self._socket.sendto(raw, (self._host, self._port))
        self._send_queue.put([data, None])

    def get_from_hub(self, data, action):
        """Send data to hub and fire action callback."""
        #_LOGGER.debug("Sending data with callback:")
        #_LOGGER.debug(data)
        #raw = self._miio_msg_encode(data)
        #self._listening = False
        #self._socket.sendto(raw, (self._host, self._port))
        #self._socket.settimeout(2)
        #try:
        #    _LOGGER.debug("Call with callback...")
        #    data = self._receive_msg()
        #    result = data.get("result")[0]
        #    action(result)
        #    self._socket.settimeout(0.1)
        #except socket.timeout:
        #    _LOGGER.debug("Call with callback timeouted for result...")
        #    action(None)
        #self._listening = True
        method = data.get("method")
        self._result_callbacks[method] = action
        self._send_queue.put([data, method])

    def listen(self):
        """Listen for messages from gateway."""
        _LOGGER.info("Starting listener...")
        self._listening = True
        #self._thread = Thread(target=self._listen_to_msg, args=())
        self._thread = Thread(target=self._run_socket_thread, args=())
        self._thread.daemon = True
        self._thread.start()
        self.send_to_hub({"method": "get_lumi_bind"})
        #self._async_track_availability()
        self._ping_callback = self._set_available

    def _run_socket_thread(self):
        while self._listening:
            if self._socket is None:
                continue
            while not self._send_queue.empty():
                msg = self._send_queue.get();
                data, unique = msg[0], msg[1]
                _LOGGER.debug("Sending data:")
                _LOGGER.debug("Callback: " + str(unique))
                raw = self._miio_msg_encode(data)
                _LOGGER.debug(raw)
                self._socket.sendto(raw, (self._host, self._port))
                # Intercept receiving if w/ callback
                if unique is not None:
                    _LOGGER.debug("Waiting for callback...")
                    self._socket.settimeout(2)
                    try:
                        _LOGGER.debug("CCC")
                        data = self._receive_msg()
                        result = data.get("result")[0]
                        self._result_callbacks[unique](result)
                        self._result_callbacks.pop(unique)
                    except socket.timeout:
                        self._result_callbacks[unique](None)
                        self._result_callbacks.pop(unique)
                    self._socket.settimeout(0.1)
            try:
                _LOGGER.debug("RCV")
                data = self._receive_msg()
                params = data.get("params")
                if isinstance(params, list):
                    params = params[0]
                self._ping_callback()
                for func in self.callbacks:
                    func(params)
            except socket.timeout:
                pass

    def close_socket(self, event):
        """Close connection socket."""
        self._listening = False
        if self._socket is not None:
            _LOGGER.info("Closing socket...")
            self._socket.close()
            self._socket = None
        self._thread.join()

    def _listen_to_msg(self):
        while self._listening:
            if self._socket is None:
                continue
            try:
                data = self._receive_msg()
                params = data.get("params")
                if isinstance(params, list):
                    params = params[0]
                self._ping_callback()
                for func in self.callbacks:
                    func(params)
            except socket.timeout:
                pass

    def _receive_msg(self):
        raw = self._socket.recvfrom(1480)[0]
        data = self._miio_msg_decode(raw)
        _LOGGER.debug("Received data:")
        _LOGGER.debug(data)
        return data

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
        return((json.dumps(msg)).encode())

    def _miio_msg_decode(self, data):
        """Decode data received from gateway."""
        if data[-1] == 0:
            data = data[:-1]
        res = {""}
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
        if was_unavailable:
            _LOGGER.info("Gateway became available!")
            for func in self.callbacks:
                func({"availability": True})

    @callback
    def _async_set_unavailable(self, now):
        """Set state to UNAVAILABLE."""
        _LOGGER.info("Gateway became unavailable by timeout!")
        self._is_available = False
        for func in self.callbacks:
            func({"availability": False})

    @callback
    def _async_send_ping(self, now):
        """Send ping to gateway."""
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
        self._get_from_hub = self._gw.get_from_hub

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
    def push_data(self, params):
        """Push data that came from gateway to parser. Update HA state if any changes were made."""
        has_availability = False
        if params is not None and params.get("availability") is not None:
            has_availability = True
        has_data = self.parse_incoming_data(params)
        if has_availability or has_data:
            self.async_schedule_update_ha_state()

    def parse_incoming_data(self, params):
        """Parse incoming data from gateway. Abstract."""
        raise NotImplementedError()
