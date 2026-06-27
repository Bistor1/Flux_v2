#!/usr/bin/env python3
"""
Flux v2 - Moderne Farbtemperatur-Steuerung
Fixed & stabilized
"""

import os
import sys
import shutil
import subprocess
import base64
import io

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
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ==================== CONSTANTS ====================
MIN_TEMP = 1000
MAX_TEMP = 6500
DEFAULT_TEMP = 3400

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
ACCENT = "#a855f7"
ACCENT_HOVER = "#c084fc"
TEXT_SECONDARY = "#888888"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
ERROR_COLOR = "#ff5555"


class FluxApp(ctk.CTk):
    def __init__(self):
        # Set appearance BEFORE creating window
        ctk.set_appearance_mode("dark")

        # className sets WM_CLASS so the window shows up as "FluxV2"
        # in Alt+Tab / taskbar instead of "tk"
        super().__init__(className="FluxV2")

        self.title("Flux v2")
        self.geometry("560x720")
        self.resizable(False, False)

        self.current_temp = DEFAULT_TEMP
        self._icon_photo = None  # CRITICAL: prevent GC of PhotoImage

        self._create_ui()
        self._set_window_icon()
        self.after(100, self._check_redshift)

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

        # --- Apply Button ---
        self.apply_btn = ctk.CTkButton(
            main,
            text="Apply Temperature",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            corner_radius=14,
            fg_color=ACCENT,
            text_color="#000000",
            hover_color=ACCENT_HOVER,
            command=self.apply_temperature
        )
        self.apply_btn.pack(pady=18, padx=30, fill="x")

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

    # ==================== FUNCTIONS ====================
    def _on_slider_change(self, value):
        temp = int(float(value))
        self.temp_display.configure(text=f"{temp}K")
        self.current_temp = temp

    def _apply_preset(self, temp):
        self.slider.set(temp)
        self.temp_display.configure(text=f"{temp}K")
        self.current_temp = temp
        self.apply_temperature()

    def _set_window_icon(self):
        """Set window icon. MUST keep PhotoImage reference to prevent GC crash."""
        if not PIL_AVAILABLE:
            return
        try:
            size = 128
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            draw.ellipse([12, 100, 116, 124], fill="#2a2a2a")
            draw.rectangle([58, 44, 70, 104], fill="#3a3a3a")
            draw.polygon([(32, 32), (96, 32), (80, 52), (48, 52)], fill=ACCENT)
            draw.ellipse([48, 48, 80, 68], fill="#c084fc")

            # Use base64 to avoid temp file issues
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            img_b64 = base64.b64encode(buf.getvalue())

            # CRITICAL: store as instance attribute to prevent garbage collection
            # If GC collects the PhotoImage, Tk's C code will segfault
            self._icon_photo = PhotoImage(data=img_b64)
            self.iconphoto(True, self._icon_photo)
        except Exception as e:
            print(f"Warning: Could not set window icon: {e}", file=sys.stderr)

    def _check_redshift(self):
        """Check if redshift is available. Delayed so UI shows first."""
        if not shutil.which("redshift"):
            messagebox.showwarning(
                "Redshift not found",
                "Redshift is not installed.\n\n"
                "Please install with:\n"
                "sudo apt install redshift"
            )

    def run_redshift(self, temp=None, reset=False, disable=False):
        try:
            if reset or disable:
                cmd = ["redshift", "-x"]
            else:
                # -P: reset previous one-shot, -O: one-shot temperature
                cmd = ["redshift", "-P", "-O", str(temp)]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return True, ""
            else:
                return False, result.stderr.strip() or "Unknown error"
        except subprocess.TimeoutExpired:
            return False, "Redshift timed out"
        except FileNotFoundError:
            return False, "Redshift is not installed"
        except Exception as e:
            return False, str(e)

    def apply_temperature(self):
        success, error = self.run_redshift(temp=self.current_temp)
        if success:
            self.status_label.configure(
                text=f"Active: {self.current_temp}K",
                text_color=SUCCESS
            )
        else:
            messagebox.showerror("Error", error)
            self.status_label.configure(text="Error", text_color=ERROR_COLOR)

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
    app = FluxApp()
    app.mainloop()