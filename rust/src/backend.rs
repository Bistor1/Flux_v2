//! Display worker. One thread owns the connection and only applies the newest temperature.

use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::thread::{self, JoinHandle};

use crate::wayland::WaylandSession;
use crate::x11::X11Session;

pub const MIN_TEMP: i32 = 1000;
pub const MAX_TEMP: i32 = 6500;

pub enum Command {
    Apply(i32),
    Restore,
    Shutdown,
}

#[derive(Clone, Debug)]
pub struct Report {
    pub applied: Option<i32>,
    pub text: String,
    pub backend: String,
}

enum Seat {
    Wayland(WaylandSession),
    X11(X11Session),
    None(String),
}

pub struct Worker {
    tx: Sender<Command>,
    rx: Receiver<Report>,
    thread: Option<JoinHandle<()>>,
}

impl Worker {
    pub fn start() -> Self {
        let (cmd_tx, cmd_rx) = mpsc::channel();
        let (rep_tx, rep_rx) = mpsc::channel();
        let thread = thread::spawn(move || run(cmd_rx, rep_tx));
        Self {
            tx: cmd_tx,
            rx: rep_rx,
            thread: Some(thread),
        }
    }

    pub fn apply(&self, temp: i32) {
        let _ = self.tx.send(Command::Apply(temp.clamp(MIN_TEMP, MAX_TEMP)));
    }

    pub fn restore(&self) {
        let _ = self.tx.send(Command::Restore);
    }

    pub fn poll(&self) -> Option<Report> {
        self.rx.try_recv().ok()
    }

    pub fn sender(&self) -> Sender<Command> {
        self.tx.clone()
    }

    pub fn shutdown(&mut self) {
        let _ = self.tx.send(Command::Shutdown);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn run(rx: Receiver<Command>, tx: Sender<Report>) {
    let mut seat = open_seat();
    let _ = tx.send(Report {
        applied: None,
        text: "Off".into(),
        backend: seat.describe(),
    });
    if std::env::var("FLUXV2_RESUME").ok().as_deref() == Some("1") {
        if let Some(temp) = crate::state::saved_temperature() {
            let _ = tx.send(apply_seat(&mut seat, temp));
        }
    }
    while let Ok(first) = rx.recv() {
        let command = latest(first, &rx);
        match command {
            Command::Apply(temp) => {
                let _ = tx.send(apply_seat(&mut seat, temp));
            }
            Command::Restore => {
                let text = match &mut seat {
                    Seat::Wayland(session) => session.restore().unwrap_or_else(|err| err),
                    Seat::X11(session) => session.restore().unwrap_or_else(|err| err),
                    Seat::None(_) => "nothing to restore".into(),
                };
                let _ = tx.send(Report {
                    applied: None,
                    text,
                    backend: seat.describe(),
                });
            }
            Command::Shutdown => {
                if std::env::var("FLUXV2_KEEP_TINT").ok().as_deref() != Some("1") {
                    let _ = match &mut seat {
                        Seat::Wayland(session) => session.restore(),
                        Seat::X11(session) => session.restore(),
                        Seat::None(_) => Ok(String::new()),
                    };
                }
                break;
            }
        }
    }
}

fn latest(first: Command, rx: &Receiver<Command>) -> Command {
    let mut current = first;
    loop {
        match rx.try_recv() {
            Ok(next) => {
                current = match (current, next) {
                    (Command::Shutdown, _) | (_, Command::Shutdown) => Command::Shutdown,
                    (Command::Restore, Command::Apply(temp)) | (Command::Apply(_), Command::Apply(temp)) => {
                        Command::Apply(temp)
                    }
                    (_, next) => next,
                };
            }
            Err(TryRecvError::Empty) | Err(TryRecvError::Disconnected) => return current,
        }
    }
}

fn open_seat() -> Seat {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        return match WaylandSession::open() {
            Ok(session) => Seat::Wayland(session),
            Err(err) => Seat::None(err),
        };
    }
    if std::env::var_os("DISPLAY").is_some() {
        return match X11Session::open() {
            Ok(session) => Seat::X11(session),
            Err(err) => Seat::None(err),
        };
    }
    Seat::None("no display".into())
}

fn apply_seat(seat: &mut Seat, temp: i32) -> Report {
    let result = match seat {
        Seat::Wayland(session) => session.apply(temp),
        Seat::X11(session) => session.apply(temp),
        Seat::None(err) => Err(err.clone()),
    };
    match result {
        Ok(text) => Report {
            applied: Some(temp),
            text,
            backend: seat.describe(),
        },
        Err(text) => Report {
            applied: None,
            text,
            backend: seat.describe(),
        },
    }
}

impl Seat {
    fn describe(&self) -> String {
        match self {
            Seat::Wayland(session) => session.describe(),
            Seat::X11(session) => session.describe(),
            Seat::None(err) => err.clone(),
        }
    }
}
