"""Widget layout. Callbacks stay on FluxApp."""

import customtkinter as ctk

from fluxv2.theme import (
    ACCENT,
    ACCENT_HOVER,
    BG_COLOR,
    CARD_BG,
    DEFAULT_TEMP,
    MAX_TEMP,
    MIN_TEMP,
    PRESETS,
    TEXT_SECONDARY,
)


def build(app):
    """Fill the window. Sets app.temp_display, app.status_label, app.slider."""
    main = ctk.CTkFrame(app, fg_color=BG_COLOR, corner_radius=0)
    main.pack(fill="both", expand=True)
    _header(main)
    _display(app, main)
    _slider(app, main)
    _presets(app, main)
    _actions(app, main)
    ctk.CTkLabel(
        main,
        text="Neutral sets 6500K. Turn Off restores the screen from before Flux. Close hides to the tray.",
        font=ctk.CTkFont(size=11),
        text_color=TEXT_SECONDARY,
        wraplength=500,
    ).pack(pady=(0, 14), padx=30)


def _header(parent):
    header = ctk.CTkFrame(parent, fg_color="transparent")
    header.pack(pady=(30, 8), padx=30, fill="x")
    ctk.CTkLabel(
        header, text="flux",
        font=ctk.CTkFont(size=42, weight="bold"),
        text_color=ACCENT,
    ).pack(side="left")
    ctk.CTkLabel(
        header, text="v2",
        font=ctk.CTkFont(size=14, weight="bold"),
        text_color=TEXT_SECONDARY,
    ).pack(side="left", padx=(4, 0), pady=(12, 0))


def _display(app, parent):
    display = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=20)
    display.pack(pady=12, padx=30, fill="x")
    app.temp_display = ctk.CTkLabel(
        display,
        text="Off",
        font=ctk.CTkFont(size=68, weight="bold"),
        text_color=TEXT_SECONDARY,
    )
    app.temp_display.pack(pady=(22, 2))
    app.status_label = ctk.CTkLabel(
        display, text="Off — screen not changed by Flux",
        font=ctk.CTkFont(size=13),
        text_color=TEXT_SECONDARY,
        wraplength=460,
    )
    app.status_label.pack(pady=(0, 18))


def _slider(app, parent):
    card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=20)
    card.pack(pady=8, padx=30, fill="x")
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(pady=18, padx=25, fill="x")

    bounds = ctk.CTkFrame(inner, fg_color="transparent")
    bounds.pack(fill="x", pady=(0, 6))
    ctk.CTkLabel(
        bounds, text=f"{MIN_TEMP}K",
        font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY,
    ).pack(side="left")
    ctk.CTkLabel(
        bounds, text=f"{MAX_TEMP}K",
        font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY,
    ).pack(side="right")

    app.slider = ctk.CTkSlider(
        inner,
        from_=MIN_TEMP,
        to=MAX_TEMP,
        number_of_steps=110,
        command=app._on_slider_change,
        progress_color=ACCENT,
        button_color=ACCENT,
        button_hover_color=ACCENT_HOVER,
        height=8,
    )
    app.slider.pack(fill="x", pady=4)
    app.slider.set(DEFAULT_TEMP)


def _presets(app, parent):
    ctk.CTkLabel(
        parent, text="Quick Presets",
        font=ctk.CTkFont(size=13, weight="bold"),
        text_color=TEXT_SECONDARY,
    ).pack(pady=(8, 6), padx=30, anchor="w")

    frame = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=20)
    frame.pack(pady=(0, 12), padx=30, fill="x")
    for i, (temp, name) in enumerate(PRESETS):
        ctk.CTkButton(
            frame,
            text=f"{temp}K\n{name}",
            width=108,
            height=50,
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=12,
            fg_color="#252525",
            hover_color="#333333",
            border_color="#333333",
            border_width=1,
            command=lambda t=temp: app._apply_preset(t),
        ).grid(row=i // 4, column=i % 4, padx=5, pady=5, sticky="ew")
    for col in range(4):
        frame.grid_columnconfigure(col, weight=1)


def _actions(app, parent):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(pady=12, padx=30, fill="x")
    ctk.CTkButton(
        frame, text="Neutral (6500K)",
        font=ctk.CTkFont(size=14, weight="bold"),
        height=44, corner_radius=12,
        fg_color="#2a2a2a", hover_color="#3a3a3a",
        command=app.reset_temperature,
    ).pack(side="left", expand=True, padx=(0, 8), fill="x")
    ctk.CTkButton(
        frame, text="Turn Off",
        font=ctk.CTkFont(size=14, weight="bold"),
        height=44, corner_radius=12,
        fg_color="#3a1f1f", hover_color="#5c2d2d",
        command=app.turn_off,
    ).pack(side="right", expand=True, padx=(8, 0), fill="x")
