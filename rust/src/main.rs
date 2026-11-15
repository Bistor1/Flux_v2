mod backend;
mod gamma;
mod icc;
mod instance;
mod kde;
mod paths;
mod state;
mod tray;
mod ui;
mod update;
mod wayland;
mod x11;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::Arc;

use crate::ui::FluxApp;

fn main() {
    if std::env::args().skip(1).any(|arg| arg == "--version") {
        println!("fluxv2-rs {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if let Some(temp) = arg_value("--apply") {
        let temp: i32 = temp.parse().unwrap_or(3400);
        match apply_once(temp) {
            Ok(text) => println!("{text}"),
            Err(err) => {
                eprintln!("{err}");
                std::process::exit(1);
            }
        }
        return;
    }
    if std::env::args().skip(1).any(|arg| arg == "--off") {
        match restore_once() {
            Ok(text) => println!("{text}"),
            Err(err) => {
                eprintln!("{err}");
                std::process::exit(1);
            }
        }
        return;
    }
    if std::env::args().skip(1).any(|arg| arg == "--probe") {
        match probe() {
            Ok(text) => {
                println!("{text}");
                return;
            }
            Err(err) => {
                eprintln!("{err}");
                std::process::exit(1);
            }
        }
    }
    if std::env::args().skip(1).any(|arg| arg == "--bench-ramp") {
        let start = std::time::Instant::now();
        let mut buf = Vec::new();
        for _ in 0..10_000 {
            gamma::write_ramp_bytes(3400, 1024, &mut buf);
        }
        println!("{:.3} ms", start.elapsed().as_secs_f64() * 1000.0);
        return;
    }
    let instance = match instance::claim() {
        Ok(owner) => owner,
        Err(()) => return,
    };
    let quit = Arc::new(AtomicBool::new(false));
    let _ = signal_hook::flag::register(signal_hook::consts::SIGTERM, Arc::clone(&quit));
    let _ = signal_hook::flag::register(signal_hook::consts::SIGHUP, Arc::clone(&quit));

    let worker = backend::Worker::start();
    let tray_tx = worker.sender();
    tray::spawn(tray_tx);

    let (show_tx, show_rx) = mpsc::channel::<()>();
    let mut app = FluxApp::new(worker);
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([560.0, 720.0])
            .with_min_inner_size([520.0, 640.0]),
        ..Default::default()
    };
    let _ = eframe::run_simple_native("Flux v2", options, move |ctx, _frame| {
        if instance.poll_show() {
            let _ = show_tx.send(());
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
            ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
        }
        if show_rx.try_recv().is_ok() {
            app.raise();
        }
        if quit.load(Ordering::Relaxed) {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
        app.paint(ctx);
    });
    app_shutdown();
}

fn arg_value(flag: &str) -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == flag {
            return args.next();
        }
        if let Some(value) = arg.strip_prefix(&format!("{flag}=")) {
            return Some(value.to_string());
        }
    }
    None
}

fn with_session<T>(body: impl FnOnce(wayland::WaylandSession) -> Result<T, String>) -> Result<T, String> {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        return body(wayland::WaylandSession::open()?);
    }
    Err("no Wayland display".into())
}

fn apply_once(temp: i32) -> Result<String, String> {
    with_session(|mut session| session.apply(temp))
}

fn restore_once() -> Result<String, String> {
    with_session(|mut session| session.restore())
}

fn probe() -> Result<String, String> {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        let session = wayland::WaylandSession::open()?;
        return Ok(session.describe());
    }
    if std::env::var_os("DISPLAY").is_some() {
        let session = x11::X11Session::open()?;
        return Ok(session.describe());
    }
    Err("no display".into())
}

fn app_shutdown() {}
