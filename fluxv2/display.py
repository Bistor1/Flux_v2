"""Apply our own gamma ramps.

Backends, in order:
  * zwlr_gamma_control_v1 when the compositor advertises it (Sway, Hyprland, …)
  * KDE output-management ICC profiles when that protocol is new enough
  * XRandR on a real X11 session

GNOME Wayland exposes neither protocol. XWayland is not used as a fallback:
its gamma ramp does not tint the Wayland outputs. A missing backend is an
error, not a silent success.
"""

import json
import os
import threading
from ctypes import (
    CDLL,
    CFUNCTYPE,
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
    memmove,
)

from fluxv2.gamma import ramp_bytes, temperature_ramps, write_temperature_profile

# kde_output_configuration_v2 request opcodes. New requests are appended, so
# these stay valid. set_icc_profile_path is 16 (since v6), set_color_profile_source
# is 19 (since v8), apply is 5. HDR profile requests arrived with v21.
_SET_ICC = 16
_SET_SOURCE = 19
_SET_HDR_ICC = 30
_SET_HDR_SOURCE = 31
_APPLY = 5
_SOURCE_SRGB = 0
_SOURCE_ICC = 1
_ICC_MIN_VERSION = 8
_HDR_REQUEST_MIN = 21
_DEVICE_REGISTRY_MIN = 21
_WL_MARSHAL_FLAG_DESTROY = 1
_x11_threads_ready = False

_lock = threading.RLock()
_session = None
_note = ""


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


class _OutputInfo(Structure):
    """Only the leading XRROutputInfo fields we read. Later fields are unused."""

    _fields_ = [
        ("timestamp", c_ulong),
        ("crtc", c_ulong),
        ("name", c_char_p),
        ("nameLen", c_int),
    ]


def _load(name):
    try:
        return CDLL(name)
    except OSError:
        return None


def init_x11_threads():
    """Call XInitThreads once, before any other Xlib use. No-op if libX11 is missing."""
    global _x11_threads_ready
    if _x11_threads_ready:
        return
    lib = _load("libX11.so.6")
    if lib is None:
        return
    lib.XInitThreads.restype = c_int
    lib.XInitThreads()
    _x11_threads_ready = True


def pending_output_names(known, bound_ids):
    """Registry names in `known` that are not yet bound. `known` is (name, version)."""
    bound = set(bound_ids)
    return [name for name, _version in known if name not in bound]


def prune_temperature_profiles(keep_path):
    """Delete Flux temperature profiles except `keep_path`. Never leaves profile_dir()."""
    directory = os.path.realpath(profile_dir())
    keep = os.path.realpath(keep_path) if keep_path else ""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith("temperature-") and name.endswith(".icc")):
            continue
        path = os.path.realpath(os.path.join(directory, name))
        if os.path.dirname(path) != directory or path == keep:
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


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
    try:
        directory = os.path.realpath(os.path.dirname(text))
    except OSError:
        return False
    return (
        directory == profile_dir()
        and os.path.basename(text).startswith("temperature-")
        and text.endswith(".icc")
    )


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


def _set_note(text):
    global _note
    _note = text or ""


def consume_note():
    """Warning from the last apply. Empty when the apply was complete."""
    global _note
    text = _note
    _note = ""
    return text


def saved_temperature():
    """Temperature stored with our tint, or None if Flux is not applied."""
    data = _read_state()
    if not data:
        return None
    try:
        return int(data["temp"])
    except (KeyError, TypeError, ValueError):
        return None


def _keep_flag():
    return os.environ.get("FLUXV2_KEEP_TINT") == "1"


class Display:
    """One Wayland connection and the outputs we are allowed to tint."""

    def __init__(self):
        self.lib = _load("libwayland-client.so.0")
        self.display = None
        self.globals = {}
        self.devices = {}
        self._keep = []
        self._registry = None
        self._management = None
        self._gamma_mgr = None
        self._outputs = []
        self._output_ids = []
        self._output_names = []
        self._config_iface = None
        self._gamma_iface = None
        self._gamma_controls = []
        self._applied = None
        self._failure = ""
        self.kind = None
        self.unavailable = ""
        self.broken = False

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
        # Variadic tail is intentionally not in argtypes. File descriptors
        # must arrive as C ints, which is how ctypes passes extra integers.
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
            self.display, 1, registry_iface, version, 0
        )
        self._listen(self._registry, ("usu", "u"), self._on_global, self._on_global_remove)
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")
        self._bind_known()
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")
        self._choose_kind()

    def close(self):
        if self.display and self.lib is not None:
            try:
                self.lib.wl_display_disconnect(self.display)
            except Exception:
                pass
        self.display = None
        self.broken = True
        self._gamma_controls = []

    def _choose_kind(self):
        if self._gamma_mgr and self._outputs:
            self.kind = "wlr"
            self.unavailable = ""
            return
        version = 0
        if self._management is not None:
            version = int(self.lib.wl_proxy_get_version(self._management))
        registry = self.globals.get("kde_output_device_registry_v2")
        registry_version = registry[1] if registry else 0
        if self._management is not None and version >= _ICC_MIN_VERSION and registry_version >= _DEVICE_REGISTRY_MIN:
            self.kind = "icc"
            self.unavailable = ""
            return
        if self._management is not None:
            self.kind = None
            self.unavailable = (
                "KWin is too old to set a color profile "
                f"(output management v{version}, need v{_ICC_MIN_VERSION})."
            )
            return
        self.kind = None
        self.unavailable = (
            "This Wayland compositor has no gamma protocol Flux can use. "
            "wlroots gamma-control and KDE Plasma work. GNOME does not."
        )

    def _listen(self, proxy, signatures, *handlers):
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

    def _on_global_remove(self, _data, _proxy, name):
        name = int(name)
        self._output_names = [item for item in self._output_names if item[0] != name]
        if name not in self._output_ids:
            return
        index = self._output_ids.index(name)
        output = self._outputs[index]
        self._destroy_gamma_for(output)
        del self._outputs[index]
        del self._output_ids[index]

    def _bind(self, interface_name, iface, version, minimum=1):
        found = self.globals.get(interface_name)
        if found is None:
            return None
        name, advertised = found
        if advertised < minimum:
            return None
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
        self._config_iface = config
        # Binding this registry below v21 is a protocol error and kills the connection.
        self._management = self._bind("kde_output_management_v2", management, 21)
        device_registry = self._bind(
            "kde_output_device_registry_v2", registry, 23, minimum=_DEVICE_REGISTRY_MIN
        )
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
        self._bind_new_outputs()

    def _bind_new_outputs(self):
        """Bind wl_output globals that arrived after the last bind. Do not rebind."""
        if not self._gamma_mgr or not self._registry or self.lib is None or not self.display:
            return []
        if "wl_output" not in self.globals:
            return []
        output_iface = c_void_p(cast(
            byref(_Interface.in_dll(self.lib, "wl_output_interface")), c_void_p
        ).value)
        added = []
        for out_name, out_version in self._output_names:
            if out_name in self._output_ids:
                continue
            use = min(4, out_version)
            proxy = self.lib.wl_proxy_marshal_flags(
                self._registry,
                0,
                output_iface,
                use,
                0,
                c_uint32(out_name),
                c_char_p(b"wl_output"),
                c_uint32(use),
                None,
            )
            if not proxy:
                continue
            self._outputs.append(proxy)
            self._output_ids.append(out_name)
            added.append(proxy)
        return added

    def _destroy_gamma_for(self, output):
        """Destroy one output's gamma object. Leave every other ramp alone."""
        kept = []
        for item in self._gamma_controls:
            if item.get("output") != output:
                kept.append(item)
                continue
            try:
                self.lib.wl_proxy_marshal_flags(
                    item["proxy"], 1, None, 1, _WL_MARSHAL_FLAG_DESTROY
                )
            except Exception:
                pass
        self._gamma_controls = kept

    def mark_broken(self):
        self.broken = True
        return False

    def _on_output(self, _data, _registry, device):
        info = {
            "proxy": device,
            "enabled": 0,
            "uuid": "",
            "name": "",
            "icc": "",
            "source": _SOURCE_SRGB,
            "hdr": 0,
            "hdr_icc": "",
            "hdr_source": _SOURCE_SRGB,
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
        handlers[19] = remember("icc", decode=True)
        handlers[23] = remember("source")
        handlers[36] = self._on_device_removed
        handlers[37] = remember("hdr_icc", decode=True)
        handlers[38] = remember("hdr_source")
        self._listen(device, sigs, *handlers)

    def _on_device_removed(self, _data, proxy):
        self.devices.pop(proxy, None)

    def enabled_devices(self):
        return [item for item in self.devices.values() if item["enabled"]]

    def apply_marked(self):
        """Send ICC changes only for devices that have a pending mark. Others stay put."""
        if self._management is None or self._config_iface is None:
            return False, "This compositor has no output configuration protocol"
        version = int(self.lib.wl_proxy_get_version(self._management))
        if version < _ICC_MIN_VERSION:
            return False, self.unavailable or "KWin cannot select an ICC profile"
        devices = [
            item for item in self.enabled_devices()
            if "_set_icc" in item or "_set_hdr_icc" in item
        ]
        if not devices:
            return True, ""
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
            if "_set_icc" in item:
                self.lib.wl_proxy_marshal_flags(
                    config, _SET_ICC, None, version, 0, proxy,
                    c_char_p(item["_set_icc"].encode()),
                )
                self.lib.wl_proxy_marshal_flags(
                    config, _SET_SOURCE, None, version, 0, proxy,
                    c_uint32(int(item["_set_source"])),
                )
                item.pop("_set_icc", None)
                item.pop("_set_source", None)
            if "_set_hdr_icc" in item and version >= _HDR_REQUEST_MIN:
                self.lib.wl_proxy_marshal_flags(
                    config, _SET_HDR_ICC, None, version, 0, proxy,
                    c_char_p(item["_set_hdr_icc"].encode()),
                )
                self.lib.wl_proxy_marshal_flags(
                    config, _SET_HDR_SOURCE, None, version, 0, proxy,
                    c_uint32(int(item["_set_hdr_source"])),
                )
            item.pop("_set_hdr_icc", None)
            item.pop("_set_hdr_source", None)
        self.lib.wl_proxy_marshal_flags(config, _APPLY, None, version, 0)
        if self.lib.wl_display_roundtrip(self.display) < 0:
            self.mark_broken()
            return False, self._failure or "Wayland rejected the color profile"
        applied = self._applied is True
        failure = self._failure
        # apply does not destroy the object. Release it so a long session
        # does not leak one compositor resource per slider tick.
        try:
            self.lib.wl_proxy_marshal_flags(
                config, 6, None, version, _WL_MARSHAL_FLAG_DESTROY
            )
            self.lib.wl_display_flush(self.display)
        except Exception:
            pass
        if not applied:
            return False, failure or "The compositor did not apply the color profile"
        return True, ""

    def _on_applied(self, _data, _proxy):
        self._applied = True

    def _on_failed(self, _data, _proxy):
        self._applied = False

    def _on_reason(self, _data, _proxy, reason):
        self._failure = reason.decode() if reason else "output configuration failed"

    def ensure_gamma(self, _retry=True):
        self._bind_new_outputs()
        live = [item for item in self._gamma_controls if not item["failed"]]
        have = {item.get("output") for item in live}
        missing = [output for output in self._outputs if output not in have]
        if live and not missing:
            return True, ""
        if not live:
            # A failed control must not be destroyed just because another
            # output appeared. Releasing here is only the all-failed path.
            self.release_gamma()
            missing = list(self._outputs)
        if not self._gamma_mgr or not missing:
            if live:
                return True, ""
            return False, "This compositor has no wlroots gamma control"
        controls = self._open_gamma(missing)
        if self.broken:
            return False, "Wayland roundtrip failed"
        accepted = [item for item in controls if item["size"] >= 2 and not item["failed"]]
        self._gamma_controls = live + accepted
        if self._gamma_controls:
            return True, ""
        if _retry and not self.broken:
            self.release_gamma()
            return self.ensure_gamma(_retry=False)
        return False, (
            "No output accepted gamma control. Another program may already "
            "own it, or this output has no gamma ramp."
        )

    def _open_gamma(self, outputs):
        controls = []
        for output in outputs:
            gamma = self.lib.wl_proxy_marshal_flags(
                self._gamma_mgr,
                0,
                cast(byref(self._gamma_iface), c_void_p),
                1,
                0,
                c_void_p(output),
            )
            if not gamma:
                continue
            info = {"proxy": gamma, "size": 0, "failed": False, "output": output}
            controls.append(info)

            def on_size(_d, _proxy, size, item=info):
                item["size"] = int(size)

            def on_failed(_d, _proxy, item=info):
                item["failed"] = True

            self._listen(gamma, ("u", ""), on_size, on_failed)
        if self.lib.wl_display_roundtrip(self.display) < 0:
            self.mark_broken()
        return controls

    def write_gamma(self, temp, _retry=True):
        sent = []
        applied = 0
        for item in self._gamma_controls:
            if item["failed"]:
                continue
            payload = ramp_bytes(temp, item["size"])
            fd = os.memfd_create("fluxv2-gamma")
            try:
                os.write(fd, payload)
                os.lseek(fd, 0, os.SEEK_SET)
                item["failed"] = False
                # Opcode 0 is set_gamma. The fd must be a C int in the variadic tail.
                self.lib.wl_proxy_marshal_flags(item["proxy"], 0, None, 1, 0, int(fd))
                # libwayland duplicates the fd when the buffer is flushed.
                if self.lib.wl_display_flush(self.display) < 0:
                    self.mark_broken()
                    return False, "Wayland flush failed"
            finally:
                os.close(fd)
            sent.append(item)
        if self.lib.wl_display_roundtrip(self.display) < 0:
            self.mark_broken()
            return False, "Wayland rejected the gamma ramp"
        for item in sent:
            if not item["failed"]:
                applied += 1
        total = len(self._gamma_controls)
        if applied == 0:
            if _retry and not self.broken:
                self.release_gamma()
                ok, err = self.ensure_gamma()
                if not ok:
                    return False, err or (
                        "The compositor rejected the gamma ramp. Another program may "
                        "already control it."
                    )
                return self.write_gamma(temp, _retry=False)
            return False, (
                "The compositor rejected the gamma ramp. Another program may "
                "already control it."
            )
        if applied < total:
            _set_note(f"{applied} of {total} outputs accepted the ramp")
        return True, ""

    def release_gamma(self):
        """Destroy gamma objects. The compositor restores its previous ramps."""
        if not self._gamma_controls or self.lib is None or not self.display:
            self._gamma_controls = []
            return
        for item in self._gamma_controls:
            try:
                self.lib.wl_proxy_marshal_flags(
                    item["proxy"], 1, None, 1, _WL_MARSHAL_FLAG_DESTROY
                )
            except Exception:
                pass
        self._gamma_controls = []
        try:
            self.lib.wl_display_flush(self.display)
        except Exception:
            pass


def _device_events(mode=None):
    """Event list in protocol order. Names are unused; signatures are not."""
    return [
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


class Session:
    """Holds the display connection for as long as a ramp must stay applied.

    wlroots restores gamma when the client disconnects, so that connection
    cannot be opened and closed on every slider tick.
    """

    def __init__(self):
        self.wayland = None
        self._xdisplay = None
        self._x11 = None
        self._randr = None
        self._x_error = False
        self._watch_x_errors = False
        self._x_handler = None
        self._prev_x_handler = None

    def ensure(self):
        if os.environ.get("WAYLAND_DISPLAY"):
            return self._ensure_wayland()
        if os.environ.get("DISPLAY"):
            return self._ensure_x11()
        return None, "No display. Flux needs a Wayland session or X11 with XRandR."

    def drop_wayland(self):
        """Close a Wayland client that can no longer be used."""
        if self.wayland is not None:
            self.wayland.close()
            self.wayland = None

    def _note_broken(self):
        if self.wayland is not None and self.wayland.broken:
            self.drop_wayland()

    def _drain(self, client):
        """Dispatch queued events, then bind outputs that just appeared."""
        if client is None or client.lib is None or not client.display:
            return False, "Wayland connection lost"
        if client.lib.wl_display_roundtrip(client.display) < 0:
            client.mark_broken()
            self.drop_wayland()
            return False, "Wayland connection lost"
        client._bind_new_outputs()
        return True, ""

    def _ensure_wayland(self):
        if self.wayland is not None and self.wayland.broken:
            self.drop_wayland()
        if self.wayland is not None and self.wayland.display and self.wayland.kind and not self.wayland.broken:
            return self.wayland.kind, ""
        if self.wayland is not None:
            reason = self.wayland.unavailable
            broken = self.wayland.broken
            self.wayland.close()
            self.wayland = None
            if reason and not broken:
                return None, reason
        client = Display()
        try:
            client.connect()
        except OSError as exc:
            client.close()
            return None, str(exc)
        self.wayland = client
        if client.kind:
            return client.kind, ""
        return None, client.unavailable or "This compositor cannot set a gamma ramp"

    def _ensure_x11(self):
        init_x11_threads()
        if self._xdisplay:
            return "x11", ""
        x11 = _load("libX11.so.6")
        randr = _load("libXrandr.so.2")
        if x11 is None or randr is None:
            return None, "XRandR is not available"
        self._bind_x11(x11, randr)
        name = os.environ.get("DISPLAY")
        display = x11.XOpenDisplay(name.encode() if name else None)
        if not display:
            return None, "could not open the X display"
        self._x11 = x11
        self._randr = randr
        self._xdisplay = display
        self._install_x_handler()
        return "x11", ""

    def _bind_x11(self, x11, randr):
        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XOpenDisplay.restype = c_void_p
        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XDefaultScreen.argtypes = [c_void_p]
        x11.XDefaultScreen.restype = c_int
        x11.XRootWindow.argtypes = [c_void_p, c_int]
        x11.XRootWindow.restype = c_ulong
        x11.XFlush.argtypes = [c_void_p]
        x11.XFlush.restype = c_int
        x11.XSync.argtypes = [c_void_p, c_int]
        x11.XSync.restype = c_int
        x11.XSetErrorHandler.argtypes = [c_void_p]
        x11.XSetErrorHandler.restype = c_void_p
        randr.XRRGetScreenResourcesCurrent.argtypes = [c_void_p, c_ulong]
        randr.XRRGetScreenResourcesCurrent.restype = POINTER(_Resources)
        randr.XRRFreeScreenResources.argtypes = [POINTER(_Resources)]
        randr.XRRGetOutputInfo.argtypes = [c_void_p, POINTER(_Resources), c_ulong]
        randr.XRRGetOutputInfo.restype = POINTER(_OutputInfo)
        randr.XRRFreeOutputInfo.argtypes = [POINTER(_OutputInfo)]
        randr.XRRGetCrtcGammaSize.argtypes = [c_void_p, c_ulong]
        randr.XRRGetCrtcGammaSize.restype = c_int
        randr.XRRGetCrtcGamma.argtypes = [c_void_p, c_ulong]
        randr.XRRGetCrtcGamma.restype = POINTER(_Gamma)
        randr.XRRAllocGamma.argtypes = [c_int]
        randr.XRRAllocGamma.restype = POINTER(_Gamma)
        randr.XRRSetCrtcGamma.argtypes = [c_void_p, c_ulong, POINTER(_Gamma)]
        randr.XRRFreeGamma.argtypes = [POINTER(_Gamma)]

    def _install_x_handler(self):
        if self._x_handler is not None:
            return
        handler_type = CFUNCTYPE(c_int, c_void_p, c_void_p)

        def on_error(display, event):
            if self._watch_x_errors:
                self._x_error = True
            previous = self._prev_x_handler
            if previous:
                return previous(display, event)
            return 0

        self._x_handler = handler_type(on_error)
        previous = self._x11.XSetErrorHandler(cast(self._x_handler, c_void_p))
        # Keep the previous handler alive and call it, or Tk's X errors vanish.
        self._prev_x_handler = handler_type(previous) if previous else None

    def apply(self, temp):
        _set_note("")
        kind, err = self.ensure()
        if not kind:
            return False, err
        temp = int(temp)
        if kind == "wlr":
            result = self._apply_wlr(temp)
        elif kind == "icc":
            result = self._apply_icc(temp)
        else:
            result = self._apply_x11(temp)
        self._note_broken()
        return result

    def restore(self):
        """Remove our tint. Does not touch a profile or ramp we did not set."""
        _set_note("")
        if _keep_flag():
            return True, ""
        saved = _read_state()
        if os.environ.get("WAYLAND_DISPLAY"):
            holding = self.wayland is not None and self.wayland._gamma_controls
            if holding:
                self.wayland.release_gamma()
                if not saved or saved.get("kind") == "wlr":
                    _clear_state()
                    return True, ""
            elif saved and saved.get("kind") == "wlr":
                # The last process already disconnected, so the compositor
                # restored its own ramps. There is nothing left to undo.
                _clear_state()
                return True, ""
            if saved and saved.get("kind") not in ("icc", None):
                _clear_state()
                return True, ""
            result = self._restore_icc(saved)
            self._note_broken()
            return result
        if os.environ.get("DISPLAY"):
            if not saved or saved.get("kind") != "x11":
                if saved:
                    _clear_state()
                return True, ""
            return self._restore_x11(saved)
        if saved:
            _clear_state()
        return True, ""

    def _apply_wlr(self, temp):
        ok, err = self._drain(self.wayland)
        if not ok:
            return False, err
        ok, err = self.wayland.ensure_gamma()
        if not ok:
            return False, err
        ok, err = self.wayland.write_gamma(temp)
        if not ok:
            return False, err
        state = _read_state()
        if not state or state.get("kind") != "wlr":
            state = {"kind": "wlr"}
        state["temp"] = temp
        _write_state(state)
        return True, ""

    def _apply_icc(self, temp):
        client = self.wayland
        ok, err = self._drain(client)
        if not ok:
            return False, err
        devices = client.enabled_devices()
        if not devices:
            return False, "No enabled outputs"
        saved = _read_state()
        if saved is None or saved.get("kind") != "icc":
            saved = {"kind": "icc", "outputs": _snapshot_icc(devices)}
            _write_state(saved)
        else:
            _fill_hdr_snapshot(saved, devices)
        path = profile_path(temp)
        try:
            write_temperature_profile(path, temp)
        except OSError as exc:
            if "liblcms2" in str(exc):
                return False, "liblcms2.so.2 is not installed. Install the liblcms2-2 package."
            return False, str(exc)
        version = int(client.lib.wl_proxy_get_version(client._management))
        hdr_set = []
        for item in devices:
            item["_set_icc"] = path
            item["_set_source"] = _SOURCE_ICC
            if int(item.get("hdr") or 0) and version >= _HDR_REQUEST_MIN:
                item["_set_hdr_icc"] = path
                item["_set_hdr_source"] = _SOURCE_ICC
                hdr_set.append(item)
        ok, err = client.apply_marked()
        if not ok:
            return False, err
        saved["temp"] = temp
        _write_state(saved)
        prune_temperature_profiles(path)
        if hdr_set:
            _set_note("HDR profile set as well")
        elif any(int(item.get("hdr") or 0) for item in devices):
            _set_note("HDR is on, so this SDR profile may not change the picture")
        return True, ""

    def _restore_icc(self, saved):
        kind, err = self._ensure_wayland()
        if kind != "icc":
            # Nothing we can put back, and we must not pretend we did.
            if saved and saved.get("kind") == "icc":
                return False, err or "Cannot restore the KDE color profile from here"
            return True, ""
        client = self.wayland
        ok, err = self._drain(client)
        if not ok:
            return False, err
        by_key = {}
        for item in (saved or {}).get("outputs") or []:
            key = _output_key(item)
            if key is not None:
                by_key[key] = item
        changed = False
        for device in client.enabled_devices():
            original = by_key.get(_output_key(device))
            current = device.get("icc") or ""
            if isinstance(current, bytes):
                current = current.decode()
            hdr_current = device.get("hdr_icc") or ""
            if isinstance(hdr_current, bytes):
                hdr_current = hdr_current.decode()
            if original is not None:
                device["_set_icc"] = original.get("icc") or ""
                device["_set_source"] = int(original.get("source", _SOURCE_SRGB))
                if "hdr_icc" in original:
                    device["_set_hdr_icc"] = original.get("hdr_icc") or ""
                    device["_set_hdr_source"] = int(original.get("hdr_source", _SOURCE_SRGB))
                changed = True
            else:
                # State file was lost. We only remove our own file, never a
                # profile the user set in System Settings.
                if _is_our_profile(current):
                    device["_set_icc"] = ""
                    device["_set_source"] = _SOURCE_SRGB
                    changed = True
                if _is_our_profile(hdr_current):
                    device["_set_hdr_icc"] = ""
                    device["_set_hdr_source"] = _SOURCE_SRGB
                    changed = True
        if not changed:
            _clear_state()
            return True, ""
        ok, err = client.apply_marked()
        if ok:
            _clear_state()
        return ok, err

    def _outputs_x11(self):
        x11 = self._x11
        randr = self._randr
        display = self._xdisplay
        root = x11.XRootWindow(display, x11.XDefaultScreen(display))
        resources = randr.XRRGetScreenResourcesCurrent(display, root)
        if not resources:
            return None, "XRandR has no outputs"
        found = []
        try:
            count = resources.contents.noutput
            for index in range(count):
                output = resources.contents.outputs[index]
                info = randr.XRRGetOutputInfo(display, resources, output)
                if not info:
                    continue
                try:
                    crtc = int(info.contents.crtc)
                    raw = info.contents.name
                    name = raw.decode() if raw else ""
                finally:
                    randr.XRRFreeOutputInfo(info)
                if not crtc or not name:
                    continue
                size = randr.XRRGetCrtcGammaSize(display, crtc)
                if size < 2:
                    continue
                found.append((name, crtc, size))
        finally:
            randr.XRRFreeScreenResources(resources)
        if not found:
            return None, "XRandR has no outputs with a gamma ramp"
        return found, ""

    def _read_gamma(self, crtc, size):
        gamma = self._randr.XRRGetCrtcGamma(self._xdisplay, crtc)
        if not gamma:
            return None
        try:
            actual = int(gamma.contents.size) or size
            return (
                [int(gamma.contents.red[i]) for i in range(actual)],
                [int(gamma.contents.green[i]) for i in range(actual)],
                [int(gamma.contents.blue[i]) for i in range(actual)],
            )
        finally:
            self._randr.XRRFreeGamma(gamma)

    def _write_gamma_x(self, crtc, red, green, blue):
        size = len(red)
        gamma = self._randr.XRRAllocGamma(size)
        if not gamma:
            return "could not allocate a gamma ramp"
        try:
            _copy_ramp(gamma.contents.red, red)
            _copy_ramp(gamma.contents.green, green)
            _copy_ramp(gamma.contents.blue, blue)
            self._x_error = False
            self._watch_x_errors = True
            try:
                self._randr.XRRSetCrtcGamma(self._xdisplay, crtc, gamma)
                self._x11.XSync(self._xdisplay, 0)
                if self._x_error:
                    return "XRandR rejected the gamma ramp"
            finally:
                self._watch_x_errors = False
        finally:
            self._randr.XRRFreeGamma(gamma)
        return ""

    def _apply_x11(self, temp):
        outputs, err = self._outputs_x11()
        if outputs is None:
            return False, err
        saved = _read_state()
        if saved is None or saved.get("kind") != "x11":
            originals = []
            for name, crtc, size in outputs:
                current = self._read_gamma(crtc, size)
                if current is None:
                    return False, f"could not read the current gamma for {name}"
                red, green, blue = current
                originals.append({
                    "name": name,
                    "red": red,
                    "green": green,
                    "blue": blue,
                })
            saved = {"kind": "x11", "outputs": originals}
            _write_state(saved)
        before = []
        for name, crtc, size in outputs:
            current = self._read_gamma(crtc, size)
            if current is None:
                return False, f"could not read the current gamma for {name}"
            before.append((name, crtc, current))
        written = []
        for name, crtc, current in before:
            red, green, blue = temperature_ramps(temp, len(current[0]))
            err = self._write_gamma_x(crtc, red, green, blue)
            if err:
                failed = self._rollback_x11(written)
                if failed:
                    return False, f"{name}: {err}; rollback failed for {failed}"
                return False, f"{name}: {err}"
            written.append((name, crtc, current))
        saved["temp"] = temp
        _write_state(saved)
        return True, ""

    def _rollback_x11(self, written):
        failed = []
        for name, crtc, ramps in reversed(written):
            err = self._write_gamma_x(crtc, ramps[0], ramps[1], ramps[2])
            if err:
                failed.append(f"{name}: {err}")
        return ", ".join(failed)

    def _restore_x11(self, saved):
        kind, err = self._ensure_x11()
        if kind != "x11":
            return False, err or "XRandR is not available"
        outputs, err = self._outputs_x11()
        if outputs is None:
            return False, err
        by_name = {item.get("name"): item for item in saved.get("outputs") or []}
        missing = []
        restored = 0
        for name, crtc, size in outputs:
            original = by_name.get(name)
            if not original:
                continue
            red, green, blue = original.get("red"), original.get("green"), original.get("blue")
            if not red or not green or not blue:
                missing.append(name)
                continue
            if len(red) != size:
                # Ramp size changed (mode switch). Identity is not the previous
                # ramp, so say so instead of pretending the restore matched.
                missing.append(name)
                continue
            err = self._write_gamma_x(crtc, red, green, blue)
            if err:
                return False, f"{name}: {err}"
            restored += 1
        if restored == 0 and by_name:
            return False, "Could not find the outputs Flux previously changed"
        if missing:
            return False, (
                "Could not restore the previous gamma for "
                + ", ".join(missing)
                + ". Those outputs are still using Flux's ramp."
            )
        _clear_state()
        return True, ""

    def close(self):
        if self.wayland is not None:
            self.wayland.close()
            self.wayland = None
        if self._xdisplay and self._x11 is not None:
            try:
                self._x11.XCloseDisplay(self._xdisplay)
            except Exception:
                pass
        self._xdisplay = None


def _output_key(item):
    uuid = item.get("uuid") or ""
    if isinstance(uuid, bytes):
        uuid = uuid.decode()
    if uuid:
        return ("uuid", uuid)
    name = item.get("name") or ""
    if isinstance(name, bytes):
        name = name.decode()
    if name:
        return ("name", name)
    return None


def _fill_hdr_snapshot(saved, devices):
    """Old state files predate HDR fields. Record them before we overwrite."""
    fresh = {}
    for item in _snapshot_icc(devices):
        key = _output_key(item)
        if key is not None:
            fresh[key] = item
    changed = False
    for item in saved.get("outputs") or []:
        if "hdr_icc" in item:
            continue
        found = fresh.get(_output_key(item))
        if found is None:
            continue
        item["hdr_icc"] = found.get("hdr_icc") or ""
        item["hdr_source"] = int(found.get("hdr_source", _SOURCE_SRGB))
        changed = True
    if changed:
        _write_state(saved)


def _snapshot_icc(devices):
    outputs = []
    for item in devices:
        icc = item.get("icc") or ""
        if isinstance(icc, bytes):
            icc = icc.decode()
        source = int(item.get("source") or _SOURCE_SRGB)
        if _is_our_profile(icc):
            icc = ""
            source = _SOURCE_SRGB
        hdr_icc = item.get("hdr_icc") or ""
        if isinstance(hdr_icc, bytes):
            hdr_icc = hdr_icc.decode()
        hdr_source = int(item.get("hdr_source") or _SOURCE_SRGB)
        if _is_our_profile(hdr_icc):
            hdr_icc = ""
            hdr_source = _SOURCE_SRGB
        outputs.append({
            "uuid": item.get("uuid") or "",
            "name": item.get("name") or "",
            "icc": icc,
            "source": source,
            "hdr_icc": hdr_icc,
            "hdr_source": hdr_source,
        })
    return outputs


def _copy_ramp(dest, values):
    buf = (c_ushort * len(values))(*values)
    memmove(dest, buf, len(values) * 2)


def _get_session():
    global _session
    if _session is None:
        _session = Session()
    return _session


def display_lock():
    return _lock


def describe_control():
    """(available, message). Message explains why when available is false."""
    with _lock:
        kind, err = _get_session().ensure()
        if kind:
            return True, ""
        return False, err or "No display Flux can tint"


def color_control_available():
    ok, _err = describe_control()
    return ok


def set_temperature(temp):
    """Apply our ramp at this temperature. 6500K is identity, not 'off'."""
    with _lock:
        try:
            return _get_session().apply(temp)
        except OSError as exc:
            return False, str(exc)


def restore_display():
    """Remove our ramp and put back what was there before the first apply."""
    with _lock:
        try:
            return _get_session().restore()
        except OSError as exc:
            return False, str(exc)
