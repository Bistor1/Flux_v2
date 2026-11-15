//! Second launch sends SHOW and exits. It does not touch the display.

use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};

use crate::paths::{ensure_dir, socket_path};

pub struct Instance {
    listener: UnixListener,
}

pub fn claim() -> Result<Instance, ()> {
    let path = socket_path();
    let _ = ensure_dir(&path);
    if let Ok(mut stream) = UnixStream::connect(&path) {
        let _ = stream.write_all(b"SHOW\n");
        let _ = stream.flush();
        return Err(());
    }
    let _ = std::fs::remove_file(&path);
    let listener = UnixListener::bind(&path).map_err(|_| ())?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    listener.set_nonblocking(true).map_err(|_| ())?;
    Ok(Instance { listener })
}

impl Instance {
    pub fn poll_show(&self) -> bool {
        match self.listener.accept() {
            Ok((mut stream, _)) => {
                let mut buf = [0; 8];
                let _ = stream.read(&mut buf);
                true
            }
            Err(_) => false,
        }
    }
}

impl Drop for Instance {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(socket_path());
    }
}
