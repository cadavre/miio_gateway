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
    _LOGGER.info("Starting gateway setup...")

    # Gateway starts it's action on object init.
    gateway = XiaomiGw(hass, config[DOMAIN][CONF_HOST], config[DOMAIN][CONF_PORT])

    # Gentle stop on HASS stop.
    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, gateway.gently_stop)

    # Share the config to platform's components.
    hass.data[DOMAIN] = gateway
    hass.data[CONF_DATA_DOMAIN] = config[DOMAIN].get(CONF_SENSORS)

    # Load components.
    for component in ["light", "media_player", "binary_sensor", "sensor", "alarm_control_panel"]:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

    # Zigbee join HASS service helper.
    def join_zigbee_service_handler(service):
        gateway = hass.data[DOMAIN]
        gateway.send_to_hub({ "method": "start_zigbee_join" })
    hass.services.register(
        DOMAIN, SERVICE_JOIN_ZIGBEE, join_zigbee_service_handler,
        schema=SERVICE_SCHEMA)

    return True

class XiaomiGw:
    """Gateway socket and communication layer."""

    def __init__(self, hass, host, port):
        self.hass = hass

        self._host = host
        self._port = port

        self._socket = None
        self._thread = None

        self._send_queue = Queue(maxsize=25)
        self._miio_id = 0

        self._callbacks = []
        self._result_callbacks = {}

        self._available = None
        self._availability_pinger = None
        self._pings_sent = 0

        self._known_sids = []
        self._known_sids.append("miio.gateway") # Append self.

        import hashlib, base64
        self._unique_id = base64.urlsafe_b64encode(hashlib.sha1((self._host + ":" + str(self._port)).encode("utf-8")).digest())[:10].decode("utf-8")

        self._create_socket()
        self._init_listener()

    """Public."""

    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    def gently_stop(self, event=None):
        """Stops listener and closes socket."""
        self._stop_listening()
        self._close_socket()

    def send_to_hub(self, data, callback=None):
        """Send data to hub."""
        miio_id, data = self._miio_msg_encode(data)
        if callback is not None:
            _LOGGER.info("Adding callback for call ID: " + str(miio_id))
            self._result_callbacks[miio_id] = callback
        self._send_queue.put(data)

    def append_callback(self, callback):
        self._callbacks.append(callback)

    def append_known_sid(self, sid):
        self._known_sids.append(sid)

    """Private."""

    def _create_socket(self):
        """Create connection socket."""
        _LOGGER.debug()("Creating socket...")
        self._socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)

    def _close_socket(self, event=None):
        """Close connection socket."""
        if self._socket is not None:
            _LOGGER.debug()("Closing socket...")
            self._socket.close()
            self._socket = None

    def _init_listener(self):
        """Initialize socket connection with first ping. Set availability accordingly."""
        try:
            # Send ping (w/o queue).
            miio_id, ping = self._miio_msg_encode({"method": "internal.PING"})
            self._socket.settimeout(0.2)
            self._socket.sendto(ping, (self._host, self._port))
            # Wait for response.
            self._socket.settimeout(5.0)
            res = self._socket.recvfrom(1480)[0]
            # If didn't timeouted - gateway is available.
            self._set_availability(True)
        except socket.timeout:
            # If timeouted – gateway is unavailable.
            self._set_availability(False)
        except (TypeError, socket.error) as e:
            # Error: gateway configuration may be wrong.
            _LOGGER.error()("Socket error! Your gateway configuration may be wrong!")
            _LOGGER.error()(e)
            self._set_availability(False)

        # We can start listener for future actions.
        if self._available is not None:
            # We have gateway initial state - now we can run loop thread that does it all.
            self._start_listening()

    def _start_listening(self):
        """Create thread for loop."""
        _LOGGER.debug("Starting thread...")
        self._thread = Thread(target=self._run_socket_thread, args=())
        #self._thread.daemon = True
        self._thread.start()
        _LOGGER.debug("Starting availability tracker...")
        self._track_availability()

    def _stop_listening(self):
        """Remove loop thread."""
        _LOGGER.debug("Exiting thread...")
        self._thread.join()

    def _run_socket_thread(self):
        """Thread loop task."""
        _LOGGER.debug("Starting listener thread...")

        while True:

            if self._socket is None:
                _LOGGER.error("No socket in listener!")
                self.create_socket()
                continue

            try:
                while not self._send_queue.empty():
                    self._socket.settimeout(0.2)
                    data = self._send_queue.get()
                    _LOGGER.debug("Sending data:")
                    _LOGGER.debug(data)
                    self._socket.sendto(data, (self._host, self._port))

                self._socket.settimeout(5)
                data = self._socket.recvfrom(1480)[0] # Will timeout on no data.

                _LOGGER.debug("Received data:")
                _LOGGER.debug(data)

                # We got here in code = we have communication with gateway.
                self._set_availability(True)

                # Get all messages from response data.
                resps = self._miio_msg_decode(data)

                # Parse all messages in response.
                self._parse_received_resps(resps)

            except socket.timeout:
                pass
            except socket.error as e:
                _LOGGER.error("Socket error!")
                _LOGGER.error(e)

    """Gateway availability."""

    def _track_availability(self):
        """Check pings status and schedule next availability check."""
        _LOGGER.debug("Starting to track availability...")
        # Schedule pings every TIME_INTERVAL_PING.
        self._availability_pinger = async_track_time_interval(
            self.hass, self._ping, TIME_INTERVAL_PING)

    def _set_availability(self, available):
        """Set availability of the gateway. Inform child devices."""
        was_available = self._available
        availability_changed = (not available and was_available) or (available and not was_available)
        if not available:
            self._available = False
            _LOGGER.warning("Gateway is unavailable!")
        else:
            self._available = True
            self._pings_sent = 0
            _LOGGER.info("Gateway is available!")

        if availability_changed:
            for func in self._callbacks:
                func(None, None, EVENT_AVAILABILITY)

    @callback
    def _ping(self):
        """Queue ping to keep and check connection."""
        self._pings_sent = self._pings_sent + 1
        self.send_to_hub({"method": "internal.PING"})
        time.sleep(6) # Give it `timeout` time to respond...
        if self._pings_sent >= 3:
            self._set_availability(False)

    """Miio gateway protocol parsing."""

    def _parse_received_resps(self, resps):
        """Parse received data."""
        for res in resps:

            if "result" in res:
                """Handling request result response."""

                miio_id = res.get("id")
                if miio_id is not None and miio_id in self._result_callbacks:

                    result = res.get("result")
                    # Convert '{"result":["ok"]}' to single value "ok".
                    if isinstance(result, list):
                        # Parse '[]' result.
                        if len(result) == 0:
                            result = "unknown"
                        else:
                            result = result[0]
                    self._result_callbacks[miio_id](result)

            elif "method" in res:
                """Handling new data received."""

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
                        params = { "data": params }

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
                    _LOGGER.info("Received unknown method: " + str(method))
                    continue

                # Now we have all the data we need
                for func in self._callbacks:
                    func(model, sid, event, params)

            else:
                """Nothing that we can handle."""
                _LOGGER.error("Non-parseable data: " + str(res))

    def _event_received(self, model, sid, event):
        """Callback for receiving sensor event from gateway."""
        _LOGGER.debug("Received event: " + str(model) + " " + str(sid) + " - " + str(event))
        if sid not in self._known_sids:
            _LOGGER.warning("Received event from unregistered sensor: " + str(model) + " " + str(sid) + " - " + str(event))

    """Miio."""

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
            msg = { "id": self._miio_id }
            msg.update(data)
        return([self._miio_id, (json.dumps(msg)).encode()])

    def _miio_msg_decode(self, data):
        """Decode data received from gateway."""

        # Trim `0` from the end of data string.
        if data[-1] == 0:
            data = data[:-1]

        # Prepare array of responses.
        resps = []
        try:
            data_arr = "[" + data.decode().replace("}{", "},{") + "]"
            resps = json.loads(data_arr)
        except:
            _LOGGER.warning("Bad JSON received: " + str(data))
        return resps


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
        self._gw.append_callback(self._add_push_data_job)

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
