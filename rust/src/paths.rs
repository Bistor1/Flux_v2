use std::path::PathBuf;

fn env_dir(key: &str, fallback: &str) -> PathBuf {
    std::env::var_os(key)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(fallback))
}

pub fn state_dir() -> PathBuf {
    env_dir("XDG_STATE_HOME", ".local/state").join("fluxv2-rs")
}

pub fn data_dir() -> PathBuf {
    env_dir("XDG_DATA_HOME", ".local/share").join("fluxv2-rs")
}

pub fn state_path() -> PathBuf {
    state_dir().join("display.json")
}

pub fn socket_path() -> PathBuf {
    state_dir().join("instance.sock")
}

pub fn profile_path(temp: i32) -> PathBuf {
    data_dir().join(format!("temperature-{temp}.icc"))
}

pub fn ensure_dir(path: &std::path::Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
