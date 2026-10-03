"""Main window. Tk calls stay on this thread; tray and IPC post into a queue."""

import base64
import io
import os
import queue
import subprocess
import sys
import threading
import time

import customtkinter as ctk
from tkinter import PhotoImage, messagebox

from fluxv2.backend import apply_temperature, color_control_available
from fluxv2.icon import PIL_AVAILABLE, Image, create_icon
from fluxv2.instance import lock_socket_path, serve_show
from fluxv2.theme import (
    DEFAULT_TEMP,
    ERROR_COLOR,
    SUCCESS,
    TEXT_SECONDARY,
    WARNING,
    temp_accent_hex,
)
from fluxv2.tray import PYSTRAY_AVAILABLE, notify, start_tray
from fluxv2.ui import build as build_ui
from fluxv2.update import current_is_outdated, install_release, relaunch_command
from fluxv2 import __version__


class FluxApp(ctk.CTk):
    _APPLY_DEBOUNCE_MS = 350

    def __init__(self, lock_socket=None):
        ctk.set_appearance_mode("dark")
        # className sets WM_CLASS so Alt+Tab shows "FluxV2" instead of "tk".
        super().__init__(className="FluxV2")

        self.title("Flux v2")
        self.geometry("560x760")
        self.resizable(False, False)

        self.current_temp = DEFAULT_TEMP
        self._icon_photo = None  # PhotoImage must stay referenced or Tk segfaults
        self._app_icon_img = None
        self._tray_icon = None
        self._quitting = False
        self._lock_socket = lock_socket
        # Workers must not call self.after(). They push callables here.
        self._ui_queue = queue.Queue()
        self._apply_after_id = None
        self._color_active = False
        self._update_declined = False
        self._update_busy = False
        self._update_prompted = False

        build_ui(self)
        self._set_window_icon()
        self.after(100, self._poll_ui_queue)
        self._setup_tray()
        self._start_ipc_listener()
        self.after(100, self._check_backend)
        self.after(1500, self._start_update_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _start_update_check(self):
        if os.environ.get("FLUXV2_NO_UPDATE"):
            return
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _update_loop(self):
        while not self._quitting:
            if not self._update_declined and not self._update_busy:
                try:
                    release = current_is_outdated()
                except Exception as exc:
                    print(f"Warning: update check failed: {exc}", file=sys.stderr)
                    release = None
                if release is not None and not self._update_prompted:
                    self._update_prompted = True
                    self._post_ui(lambda rel=release: self._ask_update(rel))
            for _ in range(30 * 60):
                if self._quitting:
                    return
                time.sleep(1)

    def _ask_update(self, release):
        if self._quitting or self._update_declined or self._update_busy:
            return
        self._show_window()
        yes = messagebox.askyesno(
            "Update Flux v2",
            f"Version {release.version} is available.\n"
            f"You have {__version__}.\n\n"
            "Do you want to update?",
            parent=self,
        )
        if not yes:
            self._update_declined = True
            return
        self._update_busy = True
        self.status_label.configure(
            text=f"Updating to {release.version}…", text_color=WARNING
        )
        notify("Flux v2", f"Installing {release.version}…")
        threading.Thread(
            target=self._install_update, args=(release,), daemon=True
        ).start()

    def _install_update(self, release):
        try:
            ok, err = install_release(release)
        except Exception as exc:
            ok, err = False, str(exc)
        if ok:
            self._post_ui(self._restart_after_update)
            return
        self._update_busy = False
        self._post_ui(lambda e=err: self._update_failed(e))

    def _update_failed(self, error):
        self.status_label.configure(
            text=self._short_error(error), text_color=ERROR_COLOR
        )
        messagebox.showerror("Update failed", error or "Update failed", parent=self)

    def _restart_after_update(self):
        """Start the installed copy and leave Night Light as it is."""
        os.environ["FLUXV2_KEEP_NIGHTLIGHT"] = "1"
        if self._lock_socket is not None:
            try:
                self._lock_socket.close()
            except Exception:
                pass
            self._lock_socket = None
        try:
            os.unlink(lock_socket_path())
        except OSError:
            pass
        command = relaunch_command()
        if command:
            child_env = os.environ.copy()
            child_env.pop("FLUXV2_KEEP_NIGHTLIGHT", None)
            subprocess.Popen(command, start_new_session=True, env=child_env)
        self._really_quit()

    def _set_temp_label(self, temp):
        self.temp_display.configure(
            text=f"{temp}K", text_color=temp_accent_hex(temp)
        )

    def _on_slider_change(self, value):
        temp = int(float(value))
        self._set_temp_label(temp)
        self.current_temp = temp
        self._schedule_live_apply()

    def _schedule_live_apply(self):
        """Apply shortly after the user stops dragging, not on every pixel."""
        if self._apply_after_id is not None:
            self.after_cancel(self._apply_after_id)
        self._apply_after_id = self.after(self._APPLY_DEBOUNCE_MS, self._live_apply)

    def _live_apply(self):
        self._apply_after_id = None
        success, error = self.run_redshift(temp=self.current_temp)
        if success:
            self.status_label.configure(
                text=f"Active: {self.current_temp}K", text_color=SUCCESS
            )
        else:
            self.status_label.configure(
                text=self._short_error(error), text_color=ERROR_COLOR
            )

    def _apply_preset(self, temp):
        # CTkSlider.set() does not fire the command, so this is the only apply.
        self.slider.set(temp)
        self._set_temp_label(temp)
        self.current_temp = temp
        self._apply_redshift()

    def _set_window_icon(self):
        if not PIL_AVAILABLE:
            return
        try:
            img = create_icon(128)
            if img is None:
                return
            self._app_icon_img = img
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._icon_photo = PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.iconphoto(True, self._icon_photo)
        except Exception as exc:
            print(f"Warning: Could not set window icon: {exc}", file=sys.stderr)

    def _check_backend(self):
        if color_control_available():
            return
        messagebox.showwarning(
            "No color control",
            "Flux v2 could not find a display to tint.\n\n"
            "A Wayland session or an X11 session with XRandR is required.",
        )

    def _setup_tray(self):
        if not PYSTRAY_AVAILABLE:
            print("Warning: pystray is not installed. System tray disabled.", file=sys.stderr)
            notify("Flux v2", "pystray not installed — system tray disabled.")
            return
        if not PIL_AVAILABLE:
            print("Warning: PIL not installed. Cannot create tray icon.", file=sys.stderr)
            return
        try:
            icon_img = create_icon(64)
            if icon_img is None:
                icon_img = Image.new("RGBA", (64, 64), (245, 158, 11, 255))
            self._app_icon_img = icon_img
            self._tray_icon = start_tray(
                icon_img,
                on_open=lambda: self._post_ui(self._show_window),
                on_quit=self._quit_from_tray,
            )
        except Exception as exc:
            print(f"Warning: Could not set up system tray: {exc}", file=sys.stderr)
            self._tray_icon = None

    def _quit_from_tray(self):
        """Tray thread. Do not touch Tk here."""
        self._quitting = True
        self._post_ui(self._really_quit)

    def _post_ui(self, callback):
        self._ui_queue.put(callback)

    def _poll_ui_queue(self):
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception as exc:
                    print(f"Warning: UI callback failed: {exc}", file=sys.stderr)
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(100, self._poll_ui_queue)
        except Exception:
            pass

    def _show_window(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _start_ipc_listener(self):
        if self._lock_socket is None:
            return
        threading.Thread(
            target=serve_show,
            args=(self._lock_socket, lambda: self._post_ui(self._show_window)),
            daemon=True,
        ).start()

    def _on_close(self):
        self.withdraw()

    def _really_quit(self):
        self._quitting = True
        self._cancel_apply()
        if self._color_active:
            apply_temperature(reset=True)
            self._color_active = False
        try:
            if self._lock_socket is not None:
                try:
                    self._lock_socket.close()
                except Exception:
                    pass
                try:
                    os.unlink(lock_socket_path())
                except OSError:
                    pass
            if self._tray_icon is not None:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass
        finally:
            self.destroy()

    def _cancel_apply(self):
        if self._apply_after_id is None:
            return
        try:
            self.after_cancel(self._apply_after_id)
        except Exception:
            pass
        self._apply_after_id = None

    def run_redshift(self, temp=None, reset=False, disable=False):
        """Apply, reset, or turn the tint off."""
        ok, err = apply_temperature(
            temp=None if reset or disable else (temp if temp is not None else self.current_temp),
            reset=reset,
            disable=disable,
        )
        self._color_active = ok and not reset and not disable
        return ok, err

    @staticmethod
    def _short_error(error):
        text = " ".join((error or "Unknown error").split())
        if len(text) > 72:
            text = text[:69] + "..."
        return text

    def _apply_redshift(self):
        success, error = self.run_redshift(temp=self.current_temp)
        if success:
            self.status_label.configure(
                text=f"Active: {self.current_temp}K", text_color=SUCCESS
            )
            return
        messagebox.showerror("Error", error)
        self.status_label.configure(
            text=self._short_error(error), text_color=ERROR_COLOR
        )

    def reset_temperature(self):
        success, error = self.run_redshift(reset=True)
        if not success:
            messagebox.showerror("Error", error)
            return
        self.status_label.configure(text="Reset to normal", text_color=TEXT_SECONDARY)
        self.slider.set(DEFAULT_TEMP)
        self._set_temp_label(DEFAULT_TEMP)
        self.current_temp = DEFAULT_TEMP

    def disable_redshift(self):
        success, error = self.run_redshift(disable=True)
        if success:
            self.status_label.configure(text="Color temperature off", text_color=WARNING)
        else:
            messagebox.showerror("Error", error)
