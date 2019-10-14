import logging
from datetime import timedelta

from homeassistant.components.media_player import MediaPlayerDevice
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_MUSIC, SUPPORT_VOLUME_SET, SUPPORT_VOLUME_MUTE, SUPPORT_PLAY_MEDIA,
    SUPPORT_PLAY, SUPPORT_STOP)
from homeassistant.const import (
    STATE_IDLE, STATE_PLAYING)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util.dt import utcnow

from . import DOMAIN, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

PLAYING_TIME = timedelta(seconds=10)

SUPPORT_PLAYER = SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | SUPPORT_PLAY_MEDIA |\
    SUPPORT_PLAY | SUPPORT_STOP

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up sound player")
    devices = []
    gateway = hass.data[DOMAIN]
    devices.append(XiaomiGatewayLight(gateway))
    add_entities(devices)

class XiaomiGatewayLight(XiaomiGwDevice, MediaPlayerDevice):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "media_player", None, "miio.gateway", "Gateway Player")
        self._volume = None
        self._muted = False
        self._ringtone = 1
        self._state = STATE_IDLE
        self._player_tracker = None

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({ "method": "get_prop", "params": ["gateway_volume"] }, self._init_set_volume)

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.info("SETTING VOL: " + str(result))
            self._volume = int(result) / 100

    def set_volume_level(self, volume):
        int_volume = int(volume * 100)
        self._send_to_hub({ "method": "set_gateway_volume", "params": [int_volume] })
        self._volume = volume
        self.schedule_update_ha_state()

    def mute_volume(self, mute):
        self._send_to_hub({ "method": "set_mute", "params": [str(mute).lower()] })
        self._muted = mute
        self.schedule_update_ha_state()

    def play_media(self, media_type, media_id, **kwargs):
        if media_type == MEDIA_TYPE_MUSIC:
            print(kwargs)
            self._ringtone = media_id
            self.media_play()

    def media_play(self, new_volume=None):
        int_volume = int(self._volume * 100)
        if new_volume is not None:
            int_volume = int(new_volume)
        self._send_to_hub({ "method": "play_music_new", "params": [str(self._ringtone), int_volume] })
        self._state = STATE_PLAYING
        self._player_tracker = async_track_point_in_utc_time(
            self.hass, self._async_playing_finished,
            utcnow() + PLAYING_TIME)
        self.schedule_update_ha_state()

    def media_stop(self):
        if self._player_tracker is not None:
            self._player_tracker()
            self._player_tracker = None
        self._send_to_hub({ "method": "set_sound_playing", "params": ["off"] })
        self._state = STATE_IDLE
        self.schedule_update_ha_state()

    def media_pause(self):
        self.media_stop()

    @property
    def state(self):
        return self._state

    @property
    def volume_level(self):
        return self._volume

    @property
    def is_volume_muted(self):
        return self._muted

    @property
    def media_artist(self):
        return "Alarm"

    @property
    def media_title(self):
        return "No " + str(self._ringtone)

    @property
    def supported_features(self):
       return SUPPORT_PLAYER

    @property
    def media_content_type(self):
        return MEDIA_TYPE_MUSIC

    @callback
    def _async_playing_finished(self, now):
        self._player_tracker = None
        self._state = STATE_IDLE
        self.async_schedule_update_ha_state()

    def parse_incoming_data(self, model, sid, event, params):

        gateway_volume = params.get("gateway_volume")
        if gateway_volume is not None:
            float_volume = gateway_volume / 100
            self._volume = float_volume
            return True

        return False
