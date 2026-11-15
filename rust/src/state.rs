use serde::{Deserialize, Serialize};

use crate::paths::{ensure_dir, state_path};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputState {
    pub uuid: String,
    pub name: String,
    #[serde(default)]
    pub icc: String,
    #[serde(default)]
    pub source: u32,
    #[serde(default)]
    pub hdr_icc: String,
    #[serde(default)]
    pub hdr_source: u32,
    #[serde(default)]
    pub crtc: u32,
    #[serde(default)]
    pub red: Vec<u16>,
    #[serde(default)]
    pub green: Vec<u16>,
    #[serde(default)]
    pub blue: Vec<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DisplayState {
    pub kind: String,
    #[serde(default)]
    pub temp: Option<i32>,
    #[serde(default)]
    pub outputs: Vec<OutputState>,
}

pub fn read_state() -> Option<DisplayState> {
    let text = std::fs::read_to_string(state_path()).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn write_state(state: &DisplayState) {
    let path = state_path();
    if ensure_dir(&path).is_err() {
        return;
    }
    let Ok(text) = serde_json::to_string(state) else {
        return;
    };
    let tmp = path.with_extension("json.tmp");
    if std::fs::write(&tmp, text).is_ok() {
        let _ = std::fs::rename(tmp, path);
    }
}

pub fn clear_state() {
    let _ = std::fs::remove_file(state_path());
}

pub fn saved_temperature() -> Option<i32> {
    read_state().and_then(|state| state.temp)
}
