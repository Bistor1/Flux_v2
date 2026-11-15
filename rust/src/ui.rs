use eframe::egui::{self, Color32, RichText, Slider, Stroke, Vec2};

use std::sync::mpsc::{self, Receiver, Sender};

use crate::backend::{Report, Worker, MAX_TEMP, MIN_TEMP};
use crate::gamma::accent;
use crate::update::{self, Release};

const PRESETS: [(i32, &str); 8] = [
    (6500, "Daylight"),
    (5500, "Paper"),
    (4200, "Cool White"),
    (3400, "Evening"),
    (3000, "Cozy"),
    (2600, "Warm"),
    (2200, "Night"),
    (1800, "Deep Red"),
];

pub struct FluxApp {
    worker: Worker,
    temp: i32,
    applied: Option<i32>,
    status: String,
    backend: String,
    show: bool,
    update_offer: Option<Release>,
    update_note: String,
    update_rx: Receiver<UpdateEvent>,
    update_tx: Sender<UpdateEvent>,
}

enum UpdateEvent {
    Offer(Release),
    Finished(Result<(), String>),
}

impl FluxApp {
    pub fn new(worker: Worker) -> Self {
        let (update_tx, update_rx) = mpsc::channel();
        let checker = update_tx.clone();
        if update::updates_enabled() {
            std::thread::spawn(move || {
                if let Ok(Some(release)) = update::fetch_latest(env!("CARGO_PKG_VERSION")) {
                    let _ = checker.send(UpdateEvent::Offer(release));
                }
            });
        }
        Self {
            worker,
            temp: MAX_TEMP,
            applied: None,
            status: "Off".into(),
            backend: "probing…".into(),
            show: true,
            update_offer: None,
            update_note: String::new(),
            update_rx,
            update_tx,
        }
    }

    pub fn raise(&mut self) {
        self.show = true;
    }

    pub fn paint(&mut self, ctx: &egui::Context) {
        while let Some(report) = self.worker.poll() {
            self.note(report);
        }
        while let Ok(event) = self.update_rx.try_recv() {
            match event {
                UpdateEvent::Offer(release) => self.update_offer = Some(release),
                UpdateEvent::Finished(Ok(())) => {
                    if update::relaunch().is_ok() {
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                }
                UpdateEvent::Finished(Err(err)) => {
                    self.update_note = err;
                    self.update_offer = None;
                }
            }
        }
        if !self.show {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
            ctx.request_repaint_after(std::time::Duration::from_millis(200));
            return;
        }
        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
        let (r, g, b) = accent(self.temp, MIN_TEMP, MAX_TEMP);
        let accent = Color32::from_rgb(r, g, b);
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.spacing_mut().item_spacing = Vec2::new(8.0, 10.0);
            if let Some(release) = self.update_offer.clone() {
                ui.horizontal(|ui| {
                    ui.label(RichText::new(format!("Update {} is available", release.version)).color(Color32::from_rgb(245, 158, 11)));
                    if ui.button("Install").clicked() {
                        let tx = self.update_tx.clone();
                        self.update_note = "Downloading the update…".into();
                        self.update_offer = None;
                        std::thread::spawn(move || {
                            let _ = tx.send(UpdateEvent::Finished(update::install(&release)));
                        });
                    }
                    if ui.button("Later").clicked() {
                        self.update_offer = None;
                    }
                });
            }
            if !self.update_note.is_empty() {
                ui.label(RichText::new(&self.update_note).color(Color32::from_rgb(255, 180, 120)));
            }
            ui.label(RichText::new("FLUX").size(12.0).color(Color32::from_rgb(136, 136, 136)));
            ui.label(RichText::new(format!("{}K", self.temp)).size(64.0).color(accent).strong());
            ui.label(RichText::new(&self.status).color(Color32::from_rgb(180, 180, 180)));
            ui.add_space(8.0);
            let slider = Slider::new(&mut self.temp, MIN_TEMP..=MAX_TEMP)
                .show_value(false)
                .trailing_fill(true);
            if ui.add_sized([ui.available_width(), 22.0], slider).changed() {
                self.worker.apply(self.temp);
            }
            ui.add_space(6.0);
            ui.horizontal_wrapped(|ui| {
                for (temp, name) in PRESETS {
                    let selected = self.applied == Some(temp);
                    let fill = if selected { accent } else { Color32::from_rgb(32, 32, 32) };
                    let text = if selected { Color32::BLACK } else { Color32::WHITE };
                    if ui
                        .add(egui::Button::new(RichText::new(name).color(text)).fill(fill).stroke(Stroke::new(1.0_f32, Color32::from_rgb(50, 50, 50))))
                        .clicked()
                    {
                        self.temp = temp;
                        self.worker.apply(temp);
                    }
                }
            });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                if ui.button("Neutral (6500K)").clicked() {
                    self.temp = 6500;
                    self.worker.apply(6500);
                }
                if ui.button("Turn Off").clicked() {
                    self.worker.restore();
                    self.applied = None;
                    self.temp = 6500;
                }
            });
            ui.add_space(12.0);
            ui.label(RichText::new(&self.backend).size(12.0).color(Color32::from_rgb(120, 120, 120)));
            ui.label(
                RichText::new("Neutral sets 6500K. Turn Off restores the screen from before Flux.")
                    .size(12.0)
                    .color(Color32::from_rgb(136, 136, 136)),
            );
        });
        ctx.request_repaint_after(std::time::Duration::from_millis(80));
    }

    fn note(&mut self, report: Report) {
        self.backend = report.backend;
        self.status = report.text;
        self.applied = report.applied;
    }
}

impl eframe::App for FluxApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.paint(ctx);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.worker.shutdown();
    }
}
