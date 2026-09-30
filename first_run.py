#!/usr/bin/env python3
"""
Flux v2 - First-Run Setup Wizard

Pure-tkinter implementation (deliberately no customtkinter dependency),
because this wizard's job is to install customtkinter (among others).

Shows a window with a list of dependencies and installs them one-by-one
with live status (pending / installing / OK / FAIL).

Invoked by the launcher when no valid venv is detected.

Env vars (set by the launcher):
  FLUXV2_VENV_DIR   -> path to the venv to create
  FLUXV2_LOG_FILE   -> path to fluxv2.log
  FLUXV2_SETUP_ERR  -> file to write a human-readable error message into
                       on failure (launcher reads it and shows zenity)
"""

import os
import sys
import subprocess
import threading

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:
    # Last-resort fallback: print and exit; launcher will surface the error.
    print("ERROR: tkinter is not installed. Run: sudo apt install python3-tk",
          file=sys.stderr)
    sys.exit(2)

# ---- Theme colors (kept in sync with the main app where possible) ----
BG_COLOR = "#0f0f0f"
CARD_BG = "#1a1a1a"
ACCENT = "#f59e0b"
ACCENT_HOVER = "#fbbf24"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#888888"
SUCCESS = "#22c55e"
ERROR_COLOR = "#ff5555"


# Each step: (label, kind, description)
# kind is interpreted by _step_cmd()
SETUP_STEPS = [
    ("Create virtual environment", "venv", "Setting up a clean Python environment for Flux v2"),
    ("Upgrade pip", "pip_upgrade", "Updating the Python package installer"),
    ("Install customtkinter", "pip_install customtkinter", "Modern GUI framework used by Flux v2"),
    ("Install Pillow", "pip_install pillow", "Image processing (logo & tray icon)"),
    ("Install pystray", "pip_install pystray", "System tray support"),
    ("Verify imports", "verify", "Ensuring all packages import correctly"),
]


def _step_cmd(kind, venv_dir):
    py = os.path.join(venv_dir, "bin", "python")
    if kind == "venv":
        return ["python3", "-m", "venv", "--system-site-packages", venv_dir]
    if kind == "pip_upgrade":
        return [py, "-m", "pip", "install", "--upgrade", "pip"]
    if kind.startswith("pip_install "):
        pkg = kind.split(" ", 1)[1]
        return [py, "-m", "pip", "install", pkg]
    if kind == "verify":
        return [py, "-c",
                "import customtkinter, PIL, tkinter, pystray"]
    raise ValueError("unknown step: " + kind)


class SetupApp(tk.Tk):
    def __init__(self, venv_dir):
        super().__init__()
        self.title("Flux v2 - Setup")
        self.geometry("540x600")
        self.resizable(False, False)
        self.configure(bg=BG_COLOR)

        self.venv_dir = venv_dir
        self._done = False
        self._success = False
        self._error_msg = ""
        self._step_widgets = []  # (icon_label, name_label, desc_label, row_frame)

        self._build_ui()

        # Start the worker thread shortly after the window appears
        self.after(300, self._run_steps_async)

    # ---------------- UI ----------------
    def _build_ui(self):
        # Try to make fonts a bit nicer (no customtkinter available here)
        try:
            title_font = tkfont.Font(size=28, weight="bold")
            sub_font = tkfont.Font(size=11)
            label_font = tkfont.Font(size=12, weight="bold")
            desc_font = tkfont.Font(size=10)
            status_font = tkfont.Font(size=11, weight="bold")
            btn_font = tkfont.Font(size=13, weight="bold")
        except Exception:
            title_font = ("TkDefaultFont", 28, "bold")
            sub_font = ("TkDefaultFont", 11)
            label_font = ("TkDefaultFont", 12, "bold")
            desc_font = ("TkDefaultFont", 10)
            status_font = ("TkDefaultFont", 11, "bold")
            btn_font = ("TkDefaultFont", 13, "bold")

        # Header (flux v2)
        header = tk.Frame(self, bg=BG_COLOR)
        header.pack(pady=(24, 2), padx=28, fill="x")
        tk.Label(
            header, text="flux", font=title_font,
            fg=ACCENT, bg=BG_COLOR
        ).pack(side="left")
        tk.Label(
            header, text="v2", font=sub_font,
            fg=TEXT_SECONDARY, bg=BG_COLOR
        ).pack(side="left", padx=(4, 0), pady=(10, 0))

        tk.Label(
            self, text="First-time setup",
            font=sub_font, fg=TEXT_SECONDARY, bg=BG_COLOR
        ).pack(pady=(0, 8), padx=28, anchor="w")

        tk.Label(
            self,
            text="Flux v2 needs a few Python packages.\nThis only happens once.",
            font=desc_font, fg=TEXT_SECONDARY, bg=BG_COLOR,
            justify="left"
        ).pack(pady=(0, 10), padx=28, anchor="w")

        # Card with steps
        card = tk.Frame(self, bg=CARD_BG, highlightthickness=0)
        card.pack(pady=4, padx=28, fill="both", expand=True)

        for i, (label, _kind, desc) in enumerate(SETUP_STEPS):
            row = tk.Frame(card, bg=CARD_BG)
            row.pack(pady=8, padx=16, fill="x")

            icon_lbl = tk.Label(
                row, text="•", width=3,
                font=label_font, fg=TEXT_SECONDARY, bg=CARD_BG
            )
            icon_lbl.pack(side="left", padx=(0, 8))

            name_lbl = tk.Label(
                row, text=label, font=label_font,
                fg=TEXT_PRIMARY, bg=CARD_BG, anchor="w"
            )
            name_lbl.pack(side="left", anchor="w")

            sub_lbl = tk.Label(
                row, text=desc, font=desc_font,
                fg=TEXT_SECONDARY, bg=CARD_BG, anchor="w"
            )
            sub_lbl.pack(side="bottom", anchor="w", pady=(2, 0))

            self._step_widgets.append((icon_lbl, name_lbl, sub_lbl, row))

        # Status line
        self.status = tk.Label(
            self, text="Preparing\u2026",
            font=status_font, fg=ACCENT, bg=BG_COLOR, anchor="w"
        )
        self.status.pack(pady=(12, 4), padx=28, fill="x")

        # Close / launch button (disabled until done)
        self.close_var = tk.StringVar(value="Please wait\u2026")
        self.close_btn = tk.Button(
            self, textvariable=self.close_var,
            state="disabled",
            font=btn_font,
            bg="#2a2a2a", fg="#666666",
            activebackground="#2a2a2a", activeforeground="#666666",
            relief="flat", bd=0,
            height=2, command=self.destroy
        )
        self.close_btn.pack(pady=(6, 20), padx=28, fill="x")

    # ---------------- Step status updates ----------------
    def _set_step(self, idx, state):
        if idx < 0 or idx >= len(self._step_widgets):
            return
        icon_lbl, name_lbl, _sub_lbl, _row = self._step_widgets[idx]
        if state == "pending":
            icon_lbl.configure(text="\u2022", fg=TEXT_SECONDARY)
            name_lbl.configure(fg=TEXT_PRIMARY)
        elif state == "running":
            icon_lbl.configure(text="\u22ef", fg=ACCENT)
            name_lbl.configure(fg=ACCENT)
        elif state == "ok":
            icon_lbl.configure(text="\u2713", fg=SUCCESS)
            name_lbl.configure(fg=TEXT_PRIMARY)
        elif state == "fail":
            icon_lbl.configure(text="\u2717", fg=ERROR_COLOR)
            name_lbl.configure(fg=ERROR_COLOR)

    def _set_status(self, text, color=ACCENT):
        self.status.configure(text=text, fg=color)

    def _enable_close_btn(self, text, bg=ACCENT, fg="#000000"):
        self.close_var.set(text)
        self.close_btn.configure(state="normal", bg=bg, fg=fg,
                                 activebackground=bg, activeforeground=fg)

    # ---------------- Background runner ----------------
    def _run_steps_async(self):
        t = threading.Thread(target=self._run_steps, daemon=True)
        t.start()

    def _run_steps(self):
        venv = self.venv_dir
        # Wipe a stale venv first (best effort)
        try:
            if os.path.isdir(venv):
                subprocess.run(["rm", "-rf", venv], check=False)
        except Exception:
            pass

        log_path = os.environ.get("FLUXV2_LOG_FILE")

        for idx, (label, kind, _desc) in enumerate(SETUP_STEPS):
            self.after(0, lambda l=label: self._set_status(l + "\u2026"))
            self.after(0, lambda i=idx: self._set_step(i, "running"))
            try:
                cmd = _step_cmd(kind, venv)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=180
                )
                if log_path:
                    try:
                        with open(log_path, "a") as f:
                            f.write(f"\n--- {label} ---\n")
                            f.write("STDOUT:\n" + (result.stdout or "") + "\n")
                            f.write("STDERR:\n" + (result.stderr or "") + "\n")
                    except Exception:
                        pass
                if result.returncode != 0:
                    err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    self.after(0, lambda i=idx, l=label, e=err: self._fail(l, e))
                    return
                self.after(0, lambda i=idx: self._set_step(i, "ok"))
            except subprocess.TimeoutExpired:
                self.after(0, lambda i=idx, l=label: self._fail(l, "Timed out"))
                return
            except Exception as e:
                self.after(0, lambda i=idx, l=label, e=e: self._fail(l, str(e)))
                return

        self.after(0, self._succeed)

    # ---------------- Termination handlers ----------------
    def _fail(self, label, msg):
        self._success = False
        self._error_msg = f"{label}: {msg}"
        self.after(0, lambda: self._set_status(f"Failed: {label}", ERROR_COLOR))
        self.after(0, lambda: self._enable_close_btn(
            "Close", bg=ERROR_COLOR, fg="#000000"))
        self._done = True

    def _succeed(self):
        self._success = True
        self.after(0, lambda: self._set_status(
            "Setup complete \u2014 launching Flux v2\u2026", SUCCESS))
        self.after(0, lambda: self._enable_close_btn(
            "Launch Flux v2", bg=ACCENT, fg="#000000"))
        # Auto-close after a short delay so the launcher continues
        self.after(800, self.destroy)
        self._done = True


def main():
    venv_dir = os.environ.get("FLUXV2_VENV_DIR")
    if not venv_dir:
        print("ERROR: FLUXV2_VENV_DIR not set", file=sys.stderr)
        sys.exit(2)

    app = SetupApp(venv_dir)
    app.mainloop()

    if app._success:
        sys.exit(0)
    else:
        err_file = os.environ.get("FLUXV2_SETUP_ERR")
        if err_file and app._error_msg:
            try:
                with open(err_file, "w") as f:
                    f.write(app._error_msg)
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
