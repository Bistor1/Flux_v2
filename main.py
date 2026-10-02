#!/usr/bin/env python3
"""
Flux v2 - Moderne Farbtemperatur-Steuerung
Fixed & stabilized
"""

import os
import queue
import sys
import socket
import shutil
import subprocess
import threading

# --- Early import checks with helpful messages ---
try:
    import customtkinter as ctk
except ImportError:
    print("ERROR: customtkinter is not installed.", file=sys.stderr)
    print("  pip install customtkinter", file=sys.stderr)
    sys.exit(1)

try:
    from tkinter import messagebox, PhotoImage
except ImportError:
    print("ERROR: tkinter is not installed.", file=sys.stderr)
    print("  sudo apt install python3-tk", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pystray
    from pystray import MenuItem as SyMenuItem, Menu as SyMenu
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False


# ==================== CONSTANTS ====================
MIN_TEMP = 1000
MAX_TEMP = 6500
DEFAULT_TEMP = 3400

# KWin's NightLight.preview() lasts ~15s and a repeat of the same value does
# not restart that timer, so the holder nudges by 1 K well before it expires.
KWIN_PREVIEW_REFRESH_MS = 8000
KWIN_SERVICE = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin/NightLight"
KWIN_IFACE = "org.kde.KWin.NightLight"

PRESETS = [
    (6500, "Daylight"),
    (5500, "Neutral"),
    (4200, "Cool White"),
    (3400, "Evening"),
    (3000, "Cozy"),
    (2600, "Warm"),
    (2200, "Night"),
    (1800, "Deep Red"),
]

BG_COLOR = "#0f0f0f"
CARD_BG = "#1a1a1a"
# Warm orange-to-amber accent (sun side) — no purple
ACCENT = "#f59e0b"
ACCENT_HOVER = "#fbbf24"
# Cool blue accent (moon side)
COOL = "#3b82f6"
COOL_HOVER = "#60a5fa"
TEXT_SECONDARY = "#888888"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
ERROR_COLOR = "#ff5555"


def create_icon(size=256):
    """Create a temperature-gradient icon: warm red center -> cool blue rim.

    A single radial gradient disc — the visual language of "color temperature"
    (red = warm/low K, blue = cool/high K). No sun, no moon, no purple.
    """
    if not PIL_AVAILABLE:
        return None
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    r = size // 2 - 4

    # --- Radial gradient: warm red (center) -> cool blue (rim) ---
    warm = (239, 68, 68)    # red-500 (warm)
    mid = (251, 146, 60)    # orange-400
    cool = (37, 99, 235)    # blue-600 (cool)
    for px in range(r, 0, -1):
        t = 1 - (px / r)       # 0 at rim, 1 at center
        if t > 0.5:
            # center -> midpoint: warm -> orange
            u = (t - 0.5) / 0.5  # 0..1
            col = (
                int(mid[0] * (1 - u) + warm[0] * u),
                int(mid[1] * (1 - u) + warm[1] * u),
                int(mid[2] * (1 - u) + warm[2] * u),
                255,
            )
        else:
            # midpoint -> rim: orange -> cool
            u = t / 0.5           # 0..1 ; 0 = rim, 1 = mid
            col = (
                int(cool[0] * (1 - u) + mid[0] * u),
                int(cool[1] * (1 - u) + mid[1] * u),
                int(cool[2] * (1 - u) + mid[2] * u),
                255,
            )
        draw.ellipse([cx - px, cy - px, cx + px, cy + px], fill=col)

    # --- Subtle inner highlight (slight glossy feel) ---
    try:
        hi = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        hdraw = ImageDraw.Draw(hi)
        hi_r = int(r * 0.55)
        hdraw.ellipse(
            [cx - hi_r, int(cy - r * 0.85), cx + hi_r, int(cy - r * 0.05)],
            fill=(255, 255, 255, 35),
        )
        hi = hi.filter(ImageFilter.GaussianBlur(radius=8))
        img.alpha_composite(hi)
    except Exception:
        pass

    return img


def _busctl(args, timeout=3):
    """Call busctl --user. Returns CompletedProcess, or None if it cannot run."""
    if not shutil.which("busctl"):
        return None
    try:
        return subprocess.run(
            ["busctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def kwin_nightlight_available():
    """True when KWin exposes Night Light (KDE Wayland/X11). Redshift cannot
    drive KWin: this compositor has no wlroots gamma protocol."""
    result = _busctl([
        "get-property", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE, "available",
    ])
    if result is None or result.returncode != 0:
        return False
    return "true" in (result.stdout or "").lower()


def kwin_preview(temp):
    """Apply a one-shot color temperature via KWin Night Light."""
    temp = int(temp)
    result = _busctl([
        "call", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE,
        "preview", "u", str(temp),
    ])
    if result is None:
        return False, "busctl is not available"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "KWin Night Light call failed").strip()
        return False, err
    return True, ""


def kwin_stop_preview():
    """Drop the Night Light preview and return the screen to normal."""
    result = _busctl([
        "call", KWIN_SERVICE, KWIN_PATH, KWIN_IFACE, "stopPreview",
    ])
    if result is None:
        return False, "busctl is not available"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "KWin stopPreview failed").strip()
        return False, err
    return True, ""


def detect_color_backend():
    """Pick a working color-temperature backend.

    KWin Night Light wins on KDE, because the redshift/gammastep binaries
    cannot set gamma there. Elsewhere we shell out to those CLIs.
    """
    if kwin_nightlight_available():
        return "kwin"
    if shutil.which("redshift"):
        return "redshift"
    if shutil.which("gammastep"):
        return "gammastep"
    return None


def _lock_socket_path():
    """Path to the single-instance IPC lock socket in the user's state dir."""
    state = os.environ.get(
        "XDG_STATE_HOME",
        os.path.expanduser("~/.local/state"),
    )
    flux_state = os.path.join(state, "fluxv2")
    try:
        os.makedirs(flux_state, exist_ok=True)
    except Exception:
        pass
    return os.path.join(flux_state, "fluxv2.sock")


def single_instance_or_signal():
    """Try to become the primary instance.

    Returns:
        socket_object : if we acquired the lock (primary instance). Caller
                        must keep the socket alive and pass it to the app.
        None           : if another instance is already running. In that case
                        we already sent it a "SHOW" message so it can pop its
                        window, and the caller should exit immediately.
    """
    sock_path = _lock_socket_path()

    # Try to connect to an existing instance first (cheap + reliable).
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(sock_path)
        s.sendall(b"SHOW")
        s.close()
        return None  # someone is listening -> not us
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
        pass

    # No one is listening. Bind our own socket. Stale socket files get cleaned.
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(None)
    # Permissions so only this user can connect
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        pass
    return srv


class FluxApp(ctk.CTk):
    def __init__(self, lock_socket=None):
        # Set appearance BEFORE creating window
        ctk.set_appearance_mode("dark")

        # className sets WM_CLASS so the window shows up as "FluxV2"
        # in Alt+Tab / taskbar instead of "tk"
        super().__init__(className="FluxV2")

        self.title("Flux v2")
        self.geometry("560x760")
        self.resizable(False, False)

        self.current_temp = DEFAULT_TEMP
        self._icon_photo = None  # CRITICAL: prevent GC of PhotoImage
        self._app_icon_img = None  # keep tray/tray-reference PIL image alive
        self._tray_icon = None
        self._quitting = False
        self._lock_socket = lock_socket  # keep alive for the lifetime of the app
        # Tk/Tcl is main-thread only. Workers push callables here; the poller
        # below runs them. Never call self.after() from another thread.
        self._ui_queue = queue.Queue()
        self._backend = None
        self._kwin_after_id = None
        self._kwin_hold = None  # temperature we must keep previewing
        self._kwin_last_sent = None

        self._create_ui()
        self._set_window_icon()
        self.after(100, self._poll_ui_queue)
        self._setup_tray()
        self._start_ipc_listener()
        self.after(100, self._check_backend)

        # Intercept window close (X) -> minimize to tray instead of quitting
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ==================== UI ====================
    def _create_ui(self):
        main = ctk.CTkFrame(self, fg_color=BG_COLOR, corner_radius=0)
        main.pack(fill="both", expand=True)

        # --- Header ---
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(pady=(30, 8), padx=30, fill="x")

        ctk.CTkLabel(
            header,
            text="flux",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=ACCENT
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="v2",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_SECONDARY
        ).pack(side="left", padx=(4, 0), pady=(12, 0))

        # --- Temperature Display ---
        display = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=20)
        display.pack(pady=12, padx=30, fill="x")

        self.temp_display = ctk.CTkLabel(
            display,
            text=f"{self.current_temp}K",
            font=ctk.CTkFont(size=68, weight="bold"),
            text_color=ACCENT
        )
        self.temp_display.pack(pady=(22, 2))

        self.status_label = ctk.CTkLabel(
            display,
            text="Ready",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_SECONDARY
        )
        self.status_label.pack(pady=(0, 18))

        # --- Slider ---
        slider_card = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=20)
        slider_card.pack(pady=8, padx=30, fill="x")

        slider_inner = ctk.CTkFrame(slider_card, fg_color="transparent")
        slider_inner.pack(pady=18, padx=25, fill="x")

        range_frame = ctk.CTkFrame(slider_inner, fg_color="transparent")
        range_frame.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(range_frame, text=f"{MIN_TEMP}K",
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_SECONDARY).pack(side="left")
        ctk.CTkLabel(range_frame, text=f"{MAX_TEMP}K",
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_SECONDARY).pack(side="right")

        self.slider = ctk.CTkSlider(
            slider_inner,
            from_=MIN_TEMP,
            to=MAX_TEMP,
            number_of_steps=110,
            command=self._on_slider_change,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            height=8
        )
        self.slider.pack(fill="x", pady=4)
        self.slider.set(DEFAULT_TEMP)

        # --- Presets ---
        ctk.CTkLabel(
            main,
            text="Quick Presets",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_SECONDARY
        ).pack(pady=(8, 6), padx=30, anchor="w")

        preset_frame = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=20)
        preset_frame.pack(pady=(0, 12), padx=30, fill="x")

        for i, (temp, name) in enumerate(PRESETS):
            btn = ctk.CTkButton(
                preset_frame,
                text=f"{temp}K\n{name}",
                width=108,
                height=50,
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=12,
                fg_color="#252525",
                hover_color="#333333",
                border_color="#333333",
                border_width=1,
                command=lambda t=temp: self._apply_preset(t)
            )
            row = i // 4
            col = i % 4
            btn.grid(row=row, column=col, padx=5, pady=5, sticky="ew")

        for col in range(4):
            preset_frame.grid_columnconfigure(col, weight=1)

        # --- Action Buttons ---
        action_frame = ctk.CTkFrame(main, fg_color="transparent")
        action_frame.pack(pady=12, padx=30, fill="x")

        ctk.CTkButton(
            action_frame,
            text="Reset to Normal",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44,
            corner_radius=12,
            fg_color="#2a2a2a",
            hover_color="#3a3a3a",
            command=self.reset_temperature
        ).pack(side="left", expand=True, padx=(0, 8), fill="x")

        ctk.CTkButton(
            action_frame,
            text="Disable Redshift",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44,
            corner_radius=12,
            fg_color="#3a1f1f",
            hover_color="#5c2d2d",
            command=self.disable_redshift
        ).pack(side="right", expand=True, padx=(8, 0), fill="x")

        # --- Minimize to tray hint ---
        ctk.CTkLabel(
            main,
            text="Closing the window minimizes to the system tray.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY
        ).pack(pady=(0, 14), padx=30)

    # ==================== FUNCTIONS ====================
    # Live-apply: redshift is spawned shortly after the user stops dragging
    # the slider, so we don't fork a process for every pixel of movement.
    _APPLY_DEBOUNCE_MS = 350
    _apply_after_id = None

    def _on_slider_change(self, value):
        temp = int(float(value))
        self.temp_display.configure(text=f"{temp}K")
        # Color shifts from warm (low) to cool (high)
        self.current_temp = temp
        if PIL_AVAILABLE:
            ratio = (temp - MIN_TEMP) / (MAX_TEMP - MIN_TEMP)
            # Interpolate accent color between warm (~1800K) and cool (~6500K)
            warm = (245, 158, 11)
            cool = (59, 130, 246)
            col = (
                int(warm[0] * (1 - ratio) + cool[0] * ratio),
                int(warm[1] * (1 - ratio) + cool[1] * ratio),
                int(warm[2] * (1 - ratio) + cool[2] * ratio),
            )
            hex_col = "#%02x%02x%02x" % col
            self.temp_display.configure(text_color=hex_col)

        self._schedule_live_apply()

    def _schedule_live_apply(self):
        """Debounce: cancel any pending apply, schedule a new one shortly."""
        if self._apply_after_id is not None:
            self.after_cancel(self._apply_after_id)
        self._apply_after_id = self.after(
            self._APPLY_DEBOUNCE_MS, self._live_apply
        )

    def _live_apply(self):
        """Apply temperature silently (no error dialog while dragging)."""
        self._apply_after_id = None
        success, error = self.run_redshift(temp=self.current_temp)
        if success:
            self.status_label.configure(
                text=f"Active: {self.current_temp}K",
                text_color=SUCCESS
            )
        else:
            # Don't pop up a messagebox mid-drag — just show status.
            self.status_label.configure(
                text=self._short_error(error), text_color=ERROR_COLOR
            )

    def _apply_preset(self, temp):
        self.slider.set(temp)
        self.temp_display.configure(text=f"{temp}K")
        self.current_temp = temp
        self._apply_redshift()

    def _set_window_icon(self):
        """Set window icon. MUST keep PhotoImage reference to prevent GC crash."""
        if not PIL_AVAILABLE:
            return
        try:
            img = create_icon(128)
            if img is None:
                return
            self._app_icon_img = img

            import io, base64
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            img_b64 = base64.b64encode(buf.getvalue())

            # CRITICAL: store as instance attribute to prevent garbage collection
            # If GC collects the PhotoImage, Tk's C code will segfault
            self._icon_photo = PhotoImage(data=img_b64)
            self.iconphoto(True, self._icon_photo)
        except Exception as e:
            print(f"Warning: Could not set window icon: {e}", file=sys.stderr)

    def _check_backend(self):
        """Warn only when no color backend exists. Delayed so UI shows first.

        On KDE this is KWin Night Light (no redshift binary required).
        """
        self._backend = detect_color_backend()
        if self._backend is not None:
            return
        messagebox.showwarning(
            "No color control",
            "Flux v2 could not find a way to set the screen temperature.\n\n"
            "On KDE, KWin Night Light is used automatically.\n"
            "Otherwise install redshift or gammastep:\n"
            "  sudo pacman -S redshift\n"
            "  sudo apt install redshift"
        )

    # ==================== SYSTEM TRAY ====================
    def _setup_tray(self):
        """Create a system tray icon with a menu. Runs pystray in a thread."""
        if not PYSTRAY_AVAILABLE:
            print("Warning: pystray is not installed. System tray disabled.", file=sys.stderr)
            self._notify("Flux v2", "pystray not installed — system tray disabled.")
            return
        if not PIL_AVAILABLE:
            print("Warning: PIL not installed. Cannot create tray icon.", file=sys.stderr)
            return

        try:
            icon_img = create_icon(64)
            if icon_img is None:
                icon_img = Image.new('RGBA', (64, 64), (245, 158, 11, 255))

            # pystray invokes these on its own thread. Do not touch Tk here.
            def _show_window_stub(icon=None):
                self._post_ui(self._show_window)

            def _quit(icon=None):
                self._quitting = True
                try:
                    if icon is not None:
                        icon.stop()
                except Exception:
                    pass
                self._post_ui(self._really_quit)

            menu = pystray.Menu(
                pystray.MenuItem("Open", _show_window_stub, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", _quit),
            )

            self._tray_icon = pystray.Icon(
                "fluxv2",
                icon_img,
                "Flux v2",
                menu,
            )

            self._tray_thread = threading.Thread(
                target=self._tray_icon.run, daemon=True
            )
            self._tray_thread.start()
        except Exception as e:
            print(f"Warning: Could not set up system tray: {e}", file=sys.stderr)
            self._tray_icon = None

    def _post_ui(self, callback):
        """Queue a callable for the Tk main thread. Safe from any thread."""
        self._ui_queue.put(callback)

    def _poll_ui_queue(self):
        """Drain callbacks queued by the IPC and tray threads."""
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception as e:
                    print(f"Warning: UI callback failed: {e}", file=sys.stderr)
        except queue.Empty:
            pass
        try:
            # destroy() inside a callback makes a further after() illegal.
            if self.winfo_exists():
                self.after(100, self._poll_ui_queue)
        except Exception:
            pass

    def _show_window(self):
        """Restore the window from minimized state. Main thread only."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _start_ipc_listener(self):
        """Listen on the lock socket so a second launch can wake us up."""
        if self._lock_socket is None:
            return

        def _serve():
            srv = self._lock_socket
            while True:
                try:
                    conn, _ = srv.accept()
                except Exception:
                    return  # socket closed -> shutting down
                try:
                    data = conn.recv(16)
                    if data == b"SHOW":
                        self._post_ui(self._show_window)
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

        t = threading.Thread(target=_serve, daemon=True)
        t.start()

    def _on_close(self):
        """Intercept WM_DELETE_WINDOW: minimize to tray instead of quitting."""
        self.withdraw()

    def _really_quit(self):
        """Actually quit the app (called from tray Quit)."""
        self._quitting = True
        self._cancel_apply()
        # Drop the KWin preview so the screen doesn't stay tinted after exit.
        if self._kwin_hold is not None:
            self._kwin_release()
        try:
            # Close IPC socket + remove the file so next launch rebinds cleanly.
            if self._lock_socket is not None:
                try:
                    self._lock_socket.close()
                except Exception:
                    pass
                try:
                    os.unlink(_lock_socket_path())
                except OSError:
                    pass
            if self._tray_icon is not None:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass
        finally:
            self.destroy()

    def _notify(self, title, msg):
        """Best-effort desktop notification (using redshift-check helper)."""
        try:
            subprocess.run(
                ["notify-send", "-a", "Flux v2", title, msg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False
            )
        except Exception:
            pass

    # ==================== COLOR TEMPERATURE ====================
    def _cancel_apply(self):
        if self._apply_after_id is not None:
            try:
                self.after_cancel(self._apply_after_id)
            except Exception:
                pass
            self._apply_after_id = None

    def _cancel_kwin_keepalive(self):
        if self._kwin_after_id is not None:
            try:
                self.after_cancel(self._kwin_after_id)
            except Exception:
                pass
            self._kwin_after_id = None

    def _schedule_kwin_keepalive(self):
        self._cancel_kwin_keepalive()
        self._kwin_after_id = self.after(
            KWIN_PREVIEW_REFRESH_MS, self._kwin_keepalive
        )

    def _kwin_keepalive(self):
        """Re-assert the preview before KWin's ~15s timeout clears it.

        preview() ignores a repeat of the same temperature, so alternate
        with ±1 K. That difference is not visible.
        """
        self._kwin_after_id = None
        if self._quitting or self._kwin_hold is None:
            return
        base = self._kwin_hold
        alt = base - 1 if base > MIN_TEMP else base + 1
        send = alt if self._kwin_last_sent == base else base
        ok, err = kwin_preview(send)
        if not ok:
            self.status_label.configure(
                text=self._short_error(err), text_color=ERROR_COLOR
            )
            return
        self._kwin_last_sent = send
        self._schedule_kwin_keepalive()

    def _kwin_hold_temp(self, temp):
        temp = int(temp)
        # Same value does not restart KWin's preview timer.
        if self._kwin_last_sent == temp:
            alt = temp - 1 if temp > MIN_TEMP else temp + 1
            ok, err = kwin_preview(alt)
            if not ok:
                return False, err
        ok, err = kwin_preview(temp)
        if not ok:
            return False, err
        self._kwin_hold = temp
        self._kwin_last_sent = temp
        self._schedule_kwin_keepalive()
        return True, ""

    def _kwin_release(self):
        self._kwin_hold = None
        self._kwin_last_sent = None
        self._cancel_kwin_keepalive()
        return kwin_stop_preview()

    def _cli_backend_bin(self):
        if self._backend == "gammastep":
            return "gammastep"
        return "redshift"

    def _run_cli_backend(self, temp=None, reset=False, disable=False):
        binary = self._cli_backend_bin()
        try:
            if reset or disable:
                cmd = [binary, "-x"]
            else:
                # -P: reset previous one-shot, -O: one-shot temperature
                cmd = [binary, "-P", "-O", str(temp)]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr.strip() or "Unknown error"
        except subprocess.TimeoutExpired:
            return False, f"{binary} timed out"
        except FileNotFoundError:
            return False, f"{binary} is not installed"
        except Exception as e:
            return False, str(e)

    def run_redshift(self, temp=None, reset=False, disable=False):
        """Apply, reset, or disable the color temperature.

        Name kept for callers. On KDE this talks to KWin Night Light;
        otherwise it shells out to redshift or gammastep.
        """
        if self._backend is None:
            self._backend = detect_color_backend()
        if self._backend is None:
            return False, (
                "No color backend. On KDE, KWin Night Light is required. "
                "Elsewhere install redshift or gammastep."
            )

        if self._backend == "kwin":
            if reset or disable:
                return self._kwin_release()
            return self._kwin_hold_temp(temp if temp is not None else self.current_temp)

        # CLI backend owns the gamma ramp; don't also hold a KWin preview.
        if self._kwin_hold is not None:
            self._kwin_release()
        return self._run_cli_backend(temp=temp, reset=reset, disable=disable)

    @staticmethod
    def _short_error(error):
        text = " ".join((error or "Unknown error").split())
        if len(text) > 72:
            text = text[:69] + "..."
        return text

    def _apply_redshift(self):
        """Apply current temperature to redshift and update the status label.

        Used internally by slider debounce + preset click. Shows an error
        dialog on failure (presets are user-initiated, so dialog is fine).
        """
        success, error = self.run_redshift(temp=self.current_temp)
        if success:
            self.status_label.configure(
                text=f"Active: {self.current_temp}K",
                text_color=SUCCESS
            )
        else:
            messagebox.showerror("Error", error)
            self.status_label.configure(
                text=self._short_error(error), text_color=ERROR_COLOR
            )

    def reset_temperature(self):
        success, error = self.run_redshift(reset=True)
        if success:
            self.status_label.configure(
                text="Reset to normal",
                text_color=TEXT_SECONDARY
            )
            self.slider.set(DEFAULT_TEMP)
            self.temp_display.configure(text=f"{DEFAULT_TEMP}K")
            self.current_temp = DEFAULT_TEMP
        else:
            messagebox.showerror("Error", error)

    def disable_redshift(self):
        success, error = self.run_redshift(disable=True)
        if success:
            self.status_label.configure(
                text="Redshift disabled",
                text_color=WARNING
            )
        else:
            messagebox.showerror("Error", error)


if __name__ == "__main__":
    lock = single_instance_or_signal()
    if lock is None:
        # Another instance is already running and we just told it to show.
        sys.exit(0)
    try:
        app = FluxApp(lock_socket=lock)
        app.mainloop()
    finally:
        try:
            lock.close()
        except Exception:
            pass
        try:
            os.unlink(_lock_socket_path())
        except OSError:
            pass
