"""Apply our own gamma ramps. Wayland first, then real X11. No redshift, no Night Light."""

import json
import os
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_int,
    c_int32,
    c_uint32,
    c_ulong,
    c_ushort,
    c_void_p,
    cast,
)

from fluxv2.gamma import channel_factors, identity_ramps, temperature_ramps, write_temperature_profile

# kde_output_configuration_v2 opcodes we send.
_SET_ICC = 16
_SET_SOURCE = 19
_SET_TRADEOFF = 21
_APPLY = 5
_SOURCE_SRGB = 0
_SOURCE_ICC = 1


class _Message(Structure):
    _fields_ = [
        ("name", c_char_p),
        ("signature", c_char_p),
        ("types", POINTER(c_void_p)),
    ]


class _Interface(Structure):
    _fields_ = [
        ("name", c_char_p),
        ("version", c_int),
        ("method_count", c_int),
        ("methods", POINTER(_Message)),
        ("event_count", c_int),
        ("events", POINTER(_Message)),
    ]


class _Gamma(Structure):
    _fields_ = [
        ("size", c_int),
        ("red", POINTER(c_ushort)),
        ("green", POINTER(c_ushort)),
        ("blue", POINTER(c_ushort)),
    ]


class _Resources(Structure):
    _fields_ = [
        ("timestamp", c_ulong),
        ("configTimestamp", c_ulong),
        ("ncrtc", c_int),
        ("crtcs", POINTER(c_ulong)),
        ("noutput", c_int),
        ("outputs", POINTER(c_ulong)),
        ("nmode", c_int),
        ("modes", c_void_p),
    ]


def _load(name):
    try:
        return CDLL(name)
    except OSError:
        return None


def _messages(rows):
    count = len(rows)
    array = (_Message * count)()
    kept = []
    for index, (name, signature, ifaces) in enumerate(rows):
        name_b = name.encode()
        sig_b = signature.encode()
        types = (c_void_p * max(len(signature), 1))()
        for slot, iface in enumerate(ifaces):
            if iface is not None:
                types[slot] = cast(byref(iface), c_void_p)
        kept.append((name_b, sig_b, types))
        array[index].name = name_b
        array[index].signature = sig_b
        array[index].types = types
    return array, kept


def _interface(name, version, requests, events):
    iface = _Interface()
    reqs, req_keep = _messages(requests)
    evts, evt_keep = _messages(events)
    name_b = name.encode()
    iface.name = name_b
    iface.version = version
    iface.method_count = len(requests)
    iface.methods = reqs
    iface.event_count = len(events)
    iface.events = evts
    iface._keep = (reqs, evts, req_keep, evt_keep, name_b)
    return iface


def _argtypes(signature):
    types = []
    for char in signature:
        if char == "i":
            types.append(c_int32)
        elif char == "u":
            types.append(c_uint32)
        elif char == "f":
            types.append(c_int32)
        elif char == "s":
            types.append(c_char_p)
        elif char in "onh":
            types.append(c_void_p)
    return types


def profile_dir():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.realpath(os.path.join(data, "fluxv2"))


def profile_path(temp):
    return os.path.join(profile_dir(), f"temperature-{int(temp)}.icc")


def _is_our_profile(path):
    text = path.decode() if isinstance(path, bytes) else (path or "")
    return os.path.basename(text).startswith("temperature-") and text.endswith(".icc")


def _state_path():
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    directory = os.path.join(state, "fluxv2")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "display.json")


def _read_state():
    path = _state_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_state(data):
    path = _state_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def _clear_state():
    try:
        os.unlink(_state_path())
    except OSError:
        pass


class Display:
    """Owns one Wayland connection and the outputs we are allowed to tint."""

    def __init__(self):
        self.lib = _load("libwayland-client.so.0")
        self.display = None
        self.globals = {}
        self.devices = {}
        self._applied = None
        self._failure = ""
        self._keep = []
        self._registry = None
        self._management = None
        self._gamma_mgr = None
        self._outputs = []
        self._output_names = []
        self._config_iface = None
        self._device_iface = None
        self._gamma_iface = None
        self._applied = None
        self._failure = ""

    def connect(self):
        if self.lib is None:
            raise OSError("libwayland-client is not available")
        lib = self.lib
        lib.wl_display_connect.argtypes = [c_char_p]
        lib.wl_display_connect.restype = c_void_p
        lib.wl_display_disconnect.argtypes = [c_void_p]
        lib.wl_display_roundtrip.argtypes = [c_void_p]
        lib.wl_display_roundtrip.restype = c_int
        lib.wl_display_flush.argtypes = [c_void_p]
        lib.wl_display_flush.restype = c_int
        lib.wl_proxy_marshal_flags.restype = c_void_p
        lib.wl_proxy_marshal_flags.argtypes = [
            c_void_p, c_uint32, c_void_p, c_uint32, c_uint32,
        ]
        lib.wl_proxy_add_listener.argtypes = [c_void_p, c_void_p, c_void_p]
        lib.wl_proxy_add_listener.restype = c_int
        lib.wl_proxy_get_version.argtypes = [c_void_p]
        lib.wl_proxy_get_version.restype = c_uint32
        name = os.environ.get("WAYLAND_DISPLAY")
        self.display = lib.wl_display_connect(name.encode() if name else None)
        if not self.display:
            raise OSError("could not connect to Wayland")
        registry_iface = c_void_p(cast(
            byref(_Interface.in_dll(lib, "wl_registry_interface")), c_void_p
        ).value)
        version = lib.wl_proxy_get_version(self.display)
        self._registry = lib.wl_proxy_marshal_flags(
            self.display, 1, registry_iface, version, 0, None
        )
        self._listen(self._registry, ("usu", "u"), self._on_global, None)
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")
        self._bind_known()
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")

    def close(self):
        if self.display and self.lib is not None:
            self.lib.wl_display_disconnect(self.display)
        self.display = None

    def _listen(self, proxy, signatures, *handlers):
        from ctypes import CFUNCTYPE

        slots = []
        for index, signature in enumerate(signatures):
            handler = handlers[index] if index < len(handlers) and handlers[index] else (lambda *_a: None)
            func = CFUNCTYPE(None, c_void_p, c_void_p, *_argtypes(signature))(handler)
            self._keep.append(func)
            slots.append(cast(func, c_void_p))
        array = (c_void_p * len(slots))(*slots)
        self._keep.append(array)
        if self.lib.wl_proxy_add_listener(proxy, array, None) != 0:
            raise OSError("could not listen on a Wayland object")

    def _on_global(self, _data, _proxy, name, interface, version):
        if not interface:
            return
        text = interface.decode()
        self.globals[text] = (int(name), int(version))
        if text == "wl_output":
            self._output_names.append((int(name), int(version)))

    def _bind(self, interface_name, iface, version):
        found = self.globals.get(interface_name)
        if found is None:
            return None
        name, advertised = found
        use = min(version, advertised)
        return self.lib.wl_proxy_marshal_flags(
            self._registry,
            0,
            cast(byref(iface), c_void_p),
            use,
            0,
            c_uint32(name),
            c_char_p(interface_name.encode()),
            c_uint32(use),
            None,
        )

    def _bind_known(self):
        mode = _interface(
            "kde_output_device_mode_v2",
            23,
            [("release", "", ())],
            [
                ("size", "ii", ()),
                ("refresh", "i", ()),
                ("preferred", "", ()),
                ("removed", "", ()),
                ("flags", "u", ()),
                ("cvt", "uuuuuuuuuuuu", ()),
            ],
        )
        device = _interface(
            "kde_output_device_v2",
            23,
            [("release", "", ())],
            _device_events(mode),
        )
        registry = _interface(
            "kde_output_device_registry_v2",
            23,
            [("stop", "", ())],
            [("", "", ()), ("output", "n", (device,))],
        )
        config = _interface(
            "kde_output_configuration_v2",
            21,
            _config_requests(device),
            [("", "", ()), ("", "", ()), ("failure_reason", "s", ())],
        )
        management = _interface(
            "kde_output_management_v2",
            21,
            [
                ("create_configuration", "n", (config,)),
                ("create_mode_list", "n", (None,)),
            ],
            [],
        )
        self._keep.extend((mode, device, registry, config, management))
        self._device_iface = device
        self._config_iface = config
        self._management = self._bind("kde_output_management_v2", management, 21)
        device_registry = self._bind("kde_output_device_registry_v2", registry, 23)
        if device_registry:
            self._listen(device_registry, ("", "n"), None, self._on_output)
        gamma = _interface(
            "zwlr_gamma_control_v1",
            1,
            [("set_gamma", "h", ()), ("destroy", "", ())],
            [("gamma_size", "u", ()), ("failed", "", ())],
        )
        manager = _interface(
            "zwlr_gamma_control_manager_v1",
            1,
            [
                ("get_gamma_control", "no", (gamma, None)),
                ("destroy", "", ()),
            ],
            [],
        )
        self._keep.extend((gamma, manager))
        self._gamma_iface = gamma
        self._gamma_mgr = self._bind("zwlr_gamma_control_manager_v1", manager, 1)
        output_iface = c_void_p(cast(
            byref(_Interface.in_dll(self.lib, "wl_output_interface")), c_void_p
        ).value)
        # wl_output is only needed for the wlroots gamma protocol.
        if self._gamma_mgr and "wl_output" in self.globals:
            name, advertised = self.globals["wl_output"]
            # Every wl_output is a separate global with its own name. The
            # registry listener only kept the last one. Re-bind from a fresh
            # pass stored in _output_names.
            for out_name, out_version in self._output_names:
                proxy = self.lib.wl_proxy_marshal_flags(
                    self._registry,
                    0,
                    output_iface,
                    min(4, out_version),
                    0,
                    c_uint32(out_name),
                    c_char_p(b"wl_output"),
                    c_uint32(min(4, out_version)),
                    None,
                )
                self._outputs.append(proxy)

    def _on_output(self, _data, _registry, device):
        info = {
            "proxy": device,
            "enabled": 0,
            "uuid": "",
            "name": "",
            "icc": "",
            "source": _SOURCE_SRGB,
        }
        self.devices[device] = info

        def remember(key, decode=False):
            def handler(_d, proxy, value):
                item = self.devices.get(proxy)
                if item is None:
                    return
                if decode:
                    item[key] = value.decode() if isinstance(value, bytes) else ""
                else:
                    item[key] = value
            return handler

        sigs = tuple(sig for _name, sig, _ifaces in _device_events(None))
        handlers = [None] * len(sigs)
        handlers[6] = remember("enabled")
        handlers[7] = remember("uuid", decode=True)
        handlers[14] = remember("name", decode=True)
        handlers[15] = remember("hdr")
        handlers[17] = remember("wcg")
        handlers[19] = remember("icc", decode=True)
        handlers[23] = remember("source")
        handlers[25] = remember("tradeoff")
        self._listen(device, sigs, *handlers)

    def enabled_devices(self):
        return [item for item in self.devices.values() if item["enabled"]]

    def apply_icc(self, path, source, tradeoff=None):
        """Point every enabled output at path, or restore a saved source."""
        if self._management is None or self._config_iface is None:
            return False, "This compositor has no output configuration protocol"
        devices = self.enabled_devices()
        if not devices:
            return False, "No enabled outputs"
        version = self.lib.wl_proxy_get_version(self._management)
        config = self.lib.wl_proxy_marshal_flags(
            self._management,
            0,
            cast(byref(self._config_iface), c_void_p),
            version,
            0,
            None,
        )
        if not config:
            return False, "Could not create an output configuration"
        self._applied = None
        self._failure = ""
        self._listen(config, ("", "", "s"), self._on_applied, self._on_failed, self._on_reason)
        for item in devices:
            proxy = c_void_p(item["proxy"])
            icc = item.get("_set_icc", path)
            src = item.get("_set_source", source)
            self.lib.wl_proxy_marshal_flags(
                config, _SET_ICC, None, version, 0, proxy, c_char_p(icc.encode()),
            )
            self.lib.wl_proxy_marshal_flags(
                config, _SET_SOURCE, None, version, 0, proxy, c_uint32(int(src)),
            )
            chosen = item.get("_set_tradeoff", tradeoff)
            if chosen is not None:
                self.lib.wl_proxy_marshal_flags(
                    config, _SET_TRADEOFF, None, version, 0, proxy, c_uint32(int(chosen)),
                )
        self.lib.wl_proxy_marshal_flags(config, _APPLY, None, version, 0)
        if self.lib.wl_display_roundtrip(self.display) < 0:
            return False, self._failure or "Wayland rejected the color profile"
        if self._applied is not True:
            return False, self._failure or "The compositor did not apply the color profile"
        return True, ""

    def _on_applied(self, _data, _proxy):
        self._applied = True

    def _on_failed(self, _data, _proxy):
        self._applied = False

    def _on_reason(self, _data, _proxy, reason):
        self._failure = reason.decode() if reason else "output configuration failed"


def _device_events(mode=None):
    """Event list in protocol order. Names are unused; signatures are not."""
    rows = [
        ("geometry", "iiiiissi", ()),
        ("current_mode", "o", (mode,)),
        ("mode", "n", (mode,)),
        ("done", "", ()),
        ("scale", "f", ()),
        ("edid", "s", ()),
        ("enabled", "i", ()),
        ("uuid", "s", ()),
        ("serial_number", "s", ()),
        ("eisa_id", "s", ()),
        ("capabilities", "u", ()),
        ("overscan", "u", ()),
        ("vrr_policy", "u", ()),
        ("rgb_range", "u", ()),
        ("name", "s", ()),
        ("high_dynamic_range", "u", ()),
        ("sdr_brightness", "u", ()),
        ("wide_color_gamut", "u", ()),
        ("auto_rotate_policy", "u", ()),
        ("icc_profile_path", "s", ()),
        ("brightness_metadata", "uuu", ()),
        ("brightness_overrides", "iii", ()),
        ("sdr_gamut_wideness", "u", ()),
        ("color_profile_source", "u", ()),
        ("brightness", "u", ()),
        ("color_power_tradeoff", "u", ()),
        ("dimming", "u", ()),
        ("replication_source", "s", ()),
        ("ddc_ci_allowed", "u", ()),
        ("max_bits_per_color", "u", ()),
        ("max_bits_per_color_range", "uu", ()),
        ("automatic_max_bits_per_color_limit", "u", ()),
        ("edr_policy", "u", ()),
        ("sharpness", "u", ()),
        ("priority", "u", ()),
        ("auto_brightness", "u", ()),
        ("removed", "", ()),
        ("hdr_icc_profile_path", "s", ()),
        ("hdr_color_profile_source", "u", ()),
        ("abm_level", "u", ()),
    ]
    return rows


def _keep_flag():
    return os.environ.get("FLUXV2_KEEP_NIGHTLIGHT") == "1"


def _neutral(temp):
    red, green, blue = channel_factors(temp)
    return min(red, green, blue) > 0.995 and max(red, green, blue) < 1.005


def set_temperature(temp):
    """Tint every output with the same Redshift factors. Returns (ok, error)."""
    if _neutral(temp):
        return restore_display()
    if os.environ.get("WAYLAND_DISPLAY"):
        return _set_wayland(temp)
    if os.environ.get("DISPLAY"):
        return _set_x11(temp)
    return False, "No display"


def restore_display():
    """Put outputs back. Safe to call when nothing was changed."""
    if _keep_flag():
        return True, ""
    saved = _read_state()
    if os.environ.get("WAYLAND_DISPLAY"):
        ok, err = _restore_wayland(saved)
    elif saved and saved.get("kind") == "x11" and os.environ.get("DISPLAY"):
        ok, err = _set_x11(None)
    else:
        ok, err = True, ""
    if ok:
        _clear_state()
    return ok, err


def color_control_available():
    if os.environ.get("WAYLAND_DISPLAY") and _load("libwayland-client.so.0"):
        return True
    if os.environ.get("DISPLAY") and _load("libXrandr.so.2") and _load("libX11.so.6"):
        return True
    return False


def _set_wayland(temp):
    client = Display()
    try:
        client.connect()
        if client._gamma_mgr and client._outputs:
            return _set_wlr(client, temp)
        if client._management is None:
            return False, "This compositor cannot set a gamma ramp"
        return _set_icc(client, temp)
    except OSError as exc:
        return False, str(exc)
    finally:
        client.close()


def _set_icc(client, temp):
    saved = _read_state()
    if saved is None:
        outputs = []
        for item in client.enabled_devices():
            icc = item["icc"] or ""
            source = int(item["source"])
            if _is_our_profile(icc):
                icc = ""
                source = _SOURCE_SRGB
            outputs.append({
                "uuid": item["uuid"],
                "name": item["name"],
                "icc": icc,
                "source": source,
            })
        saved = {"kind": "wayland", "outputs": outputs}
    path = profile_path(temp)
    try:
        write_temperature_profile(path, temp)
    except OSError as exc:
        return False, str(exc)
    ok, err = client.apply_icc(path, _SOURCE_ICC)
    if ok and _read_state() is None:
        _write_state(saved)
    return ok, err


def _restore_wayland(saved):
    client = Display()
    try:
        client.connect()
        if client._management is None:
            return False, "This compositor cannot restore the color profile"
        by_uuid = {item.get("uuid"): item for item in (saved or {}).get("outputs") or []}
        changed = False
        for device in client.enabled_devices():
            original = by_uuid.get(device["uuid"])
            current = device.get("icc") or ""
            if isinstance(current, bytes):
                current = current.decode()
            if original is None:
                if not _is_our_profile(current) and int(device.get("source") or 0) != _SOURCE_ICC:
                    continue
                device["_set_icc"] = ""
                device["_set_source"] = _SOURCE_SRGB
            else:
                device["_set_icc"] = original.get("icc") or ""
                device["_set_source"] = int(original.get("source", _SOURCE_SRGB))
            changed = True
        if not changed:
            return True, ""
        return client.apply_icc("", _SOURCE_SRGB)
    except OSError as exc:
        return False, str(exc)
    finally:
        client.close()


def _set_wlr(client, temp):
    """wlroots gamma-control. Not advertised by KWin."""
    return False, "wlroots gamma control is not implemented for this output"


def _set_x11(temp):
    x11 = _load("libX11.so.6")
    randr = _load("libXrandr.so.2")
    if x11 is None or randr is None:
        return False, "XRandR is not available"
    x11.XOpenDisplay.argtypes = [c_char_p]
    x11.XOpenDisplay.restype = c_void_p
    x11.XCloseDisplay.argtypes = [c_void_p]
    x11.XDefaultScreen.argtypes = [c_void_p]
    x11.XDefaultScreen.restype = c_int
    x11.XRootWindow.argtypes = [c_void_p, c_int]
    x11.XRootWindow.restype = c_ulong
    randr.XRRGetScreenResourcesCurrent.argtypes = [c_void_p, c_ulong]
    randr.XRRGetScreenResourcesCurrent.restype = POINTER(_Resources)
    randr.XRRFreeScreenResources.argtypes = [POINTER(_Resources)]
    randr.XRRGetCrtcGammaSize.argtypes = [c_void_p, c_ulong]
    randr.XRRGetCrtcGammaSize.restype = c_int
    randr.XRRAllocGamma.argtypes = [c_int]
    randr.XRRAllocGamma.restype = POINTER(_Gamma)
    randr.XRRSetCrtcGamma.argtypes = [c_void_p, c_ulong, POINTER(_Gamma)]
    randr.XRRFreeGamma.argtypes = [POINTER(_Gamma)]
    name = os.environ.get("DISPLAY")
    display = x11.XOpenDisplay(name.encode() if name else None)
    if not display:
        return False, "could not open the X display"
    try:
        root = x11.XRootWindow(display, x11.XDefaultScreen(display))
        resources = randr.XRRGetScreenResourcesCurrent(display, root)
        if not resources:
            return False, "XRandR has no outputs"
        try:
            count = resources.contents.ncrtc
            if count < 1:
                return False, "XRandR has no CRTCs"
            for index in range(count):
                crtc = resources.contents.crtcs[index]
                size = randr.XRRGetCrtcGammaSize(display, crtc)
                if size < 2:
                    continue
                red, green, blue = identity_ramps(size) if temp is None else temperature_ramps(temp, size)
                gamma = randr.XRRAllocGamma(size)
                if not gamma:
                    return False, "could not allocate a gamma ramp"
                try:
                    for slot, value in enumerate(red):
                        gamma.contents.red[slot] = value
                    for slot, value in enumerate(green):
                        gamma.contents.green[slot] = value
                    for slot, value in enumerate(blue):
                        gamma.contents.blue[slot] = value
                    randr.XRRSetCrtcGamma(display, crtc, gamma)
                finally:
                    randr.XRRFreeGamma(gamma)
        finally:
            randr.XRRFreeScreenResources(resources)
    finally:
        x11.XCloseDisplay(display)
    if temp is not None and _read_state() is None:
        _write_state({"kind": "x11"})
    return True, ""


def _config_requests(device):
    specs = [
        ("enable", "oi", (device,)),
        ("mode", "oo", (device, None)),
        ("transform", "oi", (device,)),
        ("position", "oii", (device,)),
        ("scale", "of", (device,)),
        ("apply", "", ()),
        ("destroy", "", ()),
        ("overscan", "ou", (device,)),
        ("set_vrr_policy", "ou", (device,)),
        ("set_rgb_range", "ou", (device,)),
        ("set_primary_output", "o", (device,)),
        ("set_priority", "ou", (device,)),
        ("set_high_dynamic_range", "ou", (device,)),
        ("set_sdr_brightness", "ou", (device,)),
        ("set_wide_color_gamut", "ou", (device,)),
        ("set_auto_rotate_policy", "ou", (device,)),
        ("set_icc_profile_path", "os", (device,)),
        ("set_brightness_overrides", "oiii", (device,)),
        ("set_sdr_gamut_wideness", "ou", (device,)),
        ("set_color_profile_source", "ou", (device,)),
        ("set_brightness", "ou", (device,)),
        ("set_color_power_tradeoff", "ou", (device,)),
        ("set_dimming", "ou", (device,)),
        ("set_replication_source", "os", (device,)),
        ("set_ddc_ci_allowed", "ou", (device,)),
        ("set_max_bits_per_color", "ou", (device,)),
        ("set_edr_policy", "ou", (device,)),
        ("set_sharpness", "ou", (device,)),
        ("set_custom_modes", "oo", (device, None)),
        ("set_auto_brightness", "ou", (device,)),
        ("set_hdr_icc_profile_path", "os", (device,)),
        ("set_hdr_color_profile_source", "ou", (device,)),
        ("set_abm_level", "ou", (device,)),
    ]
    rows = []
    for name, sig, ifaces in specs:
        padded = list(ifaces) + [None] * (len(sig) - len(ifaces))
        rows.append((name, sig, tuple(padded)))
    return rows
