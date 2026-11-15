//! XRandR gamma. One connection, reused ramp buffers, rollback if a later CRTC fails.

use x11rb::connection::{Connection, RequestConnection as _};
use x11rb::protocol::randr::{self, ConnectionExt as _};
use x11rb::rust_connection::RustConnection;

use crate::gamma::{fill_ramp_into, channel_factors};
use crate::state::{self, DisplayState, OutputState};

pub struct X11Session {
    conn: RustConnection,
    root: u32,
    outputs: Vec<Crtc>,
    scratch: Vec<u16>,
}

struct Crtc {
    id: u32,
    name: String,
    size: usize,
}

impl X11Session {
    pub fn open() -> Result<Self, String> {
        let (conn, screen) = x11rb::connect(None).map_err(|err| err.to_string())?;
        let root = conn.setup().roots[screen].root;
        if conn
            .extension_information(randr::X11_EXTENSION_NAME)
            .map_err(|err| err.to_string())?
            .is_none()
        {
            return Err("X server has no RandR extension".into());
        }
        conn.randr_query_version(1, 5)
            .map_err(|err| err.to_string())?
            .reply()
            .map_err(|err| err.to_string())?;
        let mut session = Self {
            conn,
            root,
            outputs: Vec::new(),
            scratch: Vec::new(),
        };
        session.refresh()?;
        if session.outputs.is_empty() {
            return Err("no active X11 outputs".into());
        }
        Ok(session)
    }

    pub fn describe(&self) -> String {
        format!("X11 RandR ({} outputs)", self.outputs.len())
    }

    fn refresh(&mut self) -> Result<(), String> {
        let resources = self
            .conn
            .randr_get_screen_resources_current(self.root)
            .map_err(|err| err.to_string())?
            .reply()
            .map_err(|err| err.to_string())?;
        let mut outputs = Vec::new();
        for crtc in resources.crtcs {
            let info = self
                .conn
                .randr_get_crtc_info(crtc, resources.config_timestamp)
                .map_err(|err| err.to_string())?
                .reply()
                .map_err(|err| err.to_string())?;
            if info.mode == 0 {
                continue;
            }
            let gamma = self
                .conn
                .randr_get_crtc_gamma(crtc)
                .map_err(|err| err.to_string())?
                .reply()
                .map_err(|err| err.to_string())?;
            if gamma.red.len() < 2 {
                continue;
            }
            let name = info
                .outputs
                .first()
                .and_then(|id| output_name(&self.conn, *id).ok())
                .unwrap_or_else(|| format!("crtc-{crtc}"));
            outputs.push(Crtc {
                id: crtc,
                name,
                size: gamma.red.len(),
            });
        }
        self.outputs = outputs;
        Ok(())
    }

    pub fn apply(&mut self, temp: i32) -> Result<String, String> {
        self.refresh()?;
        if self.outputs.is_empty() {
            return Err("no active X11 outputs".into());
        }
        let saved = state::read_state().filter(|state| state.kind == "x11");
        let mut before = Vec::new();
        for crtc in &self.outputs {
            let current = self.read_gamma(crtc.id, crtc.size)?;
            before.push((crtc.id, crtc.name.clone(), current));
        }
        if saved.is_none() {
            let outputs = before
                .iter()
                .map(|(id, name, (r, g, b))| OutputState {
                    uuid: String::new(),
                    name: name.clone(),
                    icc: String::new(),
                    source: 0,
                    hdr_icc: String::new(),
                    hdr_source: 0,
                    crtc: *id,
                    red: r.clone(),
                    green: g.clone(),
                    blue: b.clone(),
                })
                .collect();
            state::write_state(&DisplayState {
                kind: "x11".into(),
                temp: None,
                outputs,
            });
        }
        let (rf, gf, bf) = channel_factors(temp);
        let mut written = Vec::new();
        for (id, name, previous) in &before {
            self.write_gamma(*id, previous.0.len(), rf, gf, bf)?;
            written.push((*id, name.clone(), previous.clone()));
            if self.conn.flush().is_err() {
                self.rollback(&written)?;
                return Err(format!("{name}: X connection failed"));
            }
        }
        let mut file = state::read_state().unwrap_or(DisplayState {
            kind: "x11".into(),
            temp: None,
            outputs: Vec::new(),
        });
        file.kind = "x11".into();
        file.temp = Some(temp);
        state::write_state(&file);
        Ok(format!("X11 · {temp}K · {} outputs", self.outputs.len()))
    }

    pub fn restore(&mut self) -> Result<String, String> {
        let Some(saved) = state::read_state() else {
            return Ok("already off".into());
        };
        if saved.kind != "x11" {
            return Ok("no X11 tint stored".into());
        }
        for output in &saved.outputs {
            if output.red.len() < 2 {
                continue;
            }
            self.conn
                .randr_set_crtc_gamma(output.crtc, &output.red, &output.green, &output.blue)
                .map_err(|err| err.to_string())?;
        }
        self.conn.flush().map_err(|err| err.to_string())?;
        state::clear_state();
        Ok("restored".into())
    }

    fn read_gamma(&self, crtc: u32, size: usize) -> Result<(Vec<u16>, Vec<u16>, Vec<u16>), String> {
        let gamma = self
            .conn
            .randr_get_crtc_gamma(crtc)
            .map_err(|err| err.to_string())?
            .reply()
            .map_err(|err| err.to_string())?;
        if gamma.red.len() != size || gamma.green.len() != size || gamma.blue.len() != size {
            return Err(format!("gamma size changed on crtc {crtc}"));
        }
        Ok((gamma.red, gamma.green, gamma.blue))
    }

    fn write_gamma(&mut self, crtc: u32, size: usize, rf: f64, gf: f64, bf: f64) -> Result<(), String> {
        self.scratch.resize(size * 3, 0);
        fill_ramp_into(rf, &mut self.scratch[..size]);
        fill_ramp_into(gf, &mut self.scratch[size..size * 2]);
        fill_ramp_into(bf, &mut self.scratch[size * 2..]);
        let red = self.scratch[..size].to_vec();
        let green = self.scratch[size..size * 2].to_vec();
        let blue = self.scratch[size * 2..].to_vec();
        self.conn
            .randr_set_crtc_gamma(crtc, &red, &green, &blue)
            .map_err(|err| err.to_string())?
            .check()
            .map_err(|err| err.to_string())
    }

    fn rollback(&mut self, written: &[(u32, String, (Vec<u16>, Vec<u16>, Vec<u16>))]) -> Result<(), String> {
        for (id, name, (r, g, b)) in written.iter().rev() {
            self.conn
                .randr_set_crtc_gamma(*id, r, g, b)
                .map_err(|err| format!("{name}: rollback failed: {err}"))?;
        }
        self.conn.flush().map_err(|err| err.to_string())?;
        Ok(())
    }
}

fn output_name(conn: &RustConnection, output: u32) -> Result<String, String> {
    let reply = conn
        .randr_get_output_info(output, 0)
        .map_err(|err| err.to_string())?
        .reply()
        .map_err(|err| err.to_string())?;
    Ok(String::from_utf8_lossy(&reply.name).into_owned())
}
