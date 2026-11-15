//! StatusNotifier tray. The window stays the way out if the host has no watcher.

use std::sync::mpsc::Sender;

use crate::backend::Command;

pub fn spawn(tx: Sender<Command>) {
    // The watcher name is what KDE listens for. A missing watcher is not fatal:
    // the window still has Turn Off, and close quits this process.
    if std::env::var_os("FLUXV2_NO_TRAY").is_some() {
        drop(tx);
        return;
    }
    std::thread::spawn(move || {
        if let Err(err) = run(tx) {
            eprintln!("fluxv2-rs tray: {err}");
        }
    });
}

fn run(tx: Sender<Command>) -> Result<(), String> {
    // Kept as a channel owner so a later StatusNotifier backend can send Apply/Restore
    // without the UI thread taking the Wayland display lock.
    let _ = tx;
    Ok(())
}
