import logging
import binascii
import struct

from homeassistant.components.light import (
    Light, ATTR_BRIGHTNESS, ATTR_HS_COLOR, SUPPORT_BRIGHTNESS, SUPPORT_COLOR)
import homeassistant.util.color as color_util

from . import DOMAIN, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_entities, discovery_info=None):
    _LOGGER.info("Setting up light")
    devices = []
    gateway = hass.data[DOMAIN]
    devices.append(XiaomiGatewayLight(gateway))
    add_entities(devices)

class XiaomiGatewayLight(XiaomiGwDevice, Light):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "light", None, "miio.gateway", "Gateway LED")
        self._hs = (0, 0)
        self._brightness = 100
        self._state = False

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({ "method": "toggle_light", "params": ["off"] })

    @property
    def is_on(self):
        return self._state

    @property
    def brightness(self):
        return int(255 * self._brightness / 100)

    @property
    def hs_color(self):
        return self._hs

    @property
    def supported_features(self):
        return SUPPORT_BRIGHTNESS | SUPPORT_COLOR
    
    def turn_on(self, **kwargs):
        if ATTR_HS_COLOR in kwargs:
            self._hs = kwargs[ATTR_HS_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = int(100 * kwargs[ATTR_BRIGHTNESS] / 255)
        rgb = color_util.color_hs_to_RGB(*self._hs)
        argb = (self._brightness,) + rgb
        argbhex = binascii.hexlify(struct.pack("BBBB", *argb)).decode("ASCII")
        argbhex = int(argbhex, 16)
        
        if argbhex <= 16777215:
            self._send_to_hub({ "method": "toggle_light", "params": ["on"] })
        else:
            self._send_to_hub({ "method": "set_rgb", "params": [argbhex] })
        
        self._state = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        self._send_to_hub({ "method": "toggle_light", "params": ["off"] })
        self._state = False
        self.schedule_update_ha_state()

    def parse_incoming_data(self, model, sid, event, params):

        light = params.get("light")
        rgba_raw = params.get("rgb")
        
        if None not in (rgba_raw, light):
            rgbhexstr = "%x" % rgba_raw
            if len(rgbhexstr) <= 8:
                rgbhexstr = rgbhexstr.zfill(8)
                rgbhex = bytes.fromhex(rgbhexstr)
                rgba = struct.unpack('BBBB', rgbhex)
                brightness = rgba[0]
                rgb = rgba[1:]
        
                if light == 'on' or rgba_raw > 0:
                    self._state = True
                    self._brightness = brightness
                    self._hs = color_util.color_RGB_to_hs(*rgb)
                else:
                    self._state = False
                return True

        return False
