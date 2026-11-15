//! GitHub release check. Installs only a digest-verified package for this system.

use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;
use std::process::Command;

pub const REPO: &str = "Bistor1/Flux_v2";

const ALLOWED: &[&str] = &[
    "https://github.com/",
    "https://release-assets.githubusercontent.com/",
    "https://objects.githubusercontent.com/",
    "https://github-releases.githubusercontent.com/",
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PackageKind {
    Rpm,
    Deb,
    Flatpak,
}

#[derive(Clone, Debug)]
pub struct Release {
    pub version: String,
    pub url: String,
    pub digest: String,
    pub kind: PackageKind,
}

pub fn parse_version(text: &str) -> Vec<u32> {
    let mut parts = Vec::new();
    for piece in text.trim().trim_start_matches(['v', 'V']).split('.') {
        let digits: String = piece.chars().take_while(|c| c.is_ascii_digit()).collect();
        if digits.is_empty() {
            break;
        }
        parts.push(digits.parse().unwrap_or(0));
    }
    parts
}

pub fn is_newer(remote: &str, local: &str) -> bool {
    parse_version(remote) > parse_version(local)
}

pub fn safe_url(url: &str) -> bool {
    ALLOWED.iter().any(|prefix| url.starts_with(prefix))
}

pub fn digest_hex(value: &str) -> Option<String> {
    let rest = value.strip_prefix("sha256:").or_else(|| value.strip_prefix("SHA256:"))?;
    let hex = rest.trim().to_ascii_lowercase();
    if hex.len() == 64 && hex.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(hex)
    } else {
        None
    }
}

pub fn asset_name(version: &str, kind: &PackageKind) -> String {
    match kind {
        PackageKind::Rpm => format!("fluxv2-{version}-1.fc43.x86_64.rpm"),
        PackageKind::Deb => format!("fluxv2_{version}_amd64.deb"),
        PackageKind::Flatpak => format!("fluxv2_{version}.flatpak"),
    }
}

pub fn updates_enabled() -> bool {
    if std::env::var_os("FLUXV2_NO_UPDATE").is_some() {
        return false;
    }
    package_kind().is_some()
}

pub fn package_kind() -> Option<PackageKind> {
    if std::env::var_os("FLATPAK_ID").is_some() {
        return Some(PackageKind::Flatpak);
    }
    if command_ok("rpm", &["-q", "fluxv2"]) {
        return Some(PackageKind::Rpm);
    }
    if command_ok("dpkg", &["-s", "fluxv2"]) {
        return Some(PackageKind::Deb);
    }
    None
}

fn command_ok(program: &str, args: &[&str]) -> bool {
    Command::new(program)
        .args(args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

pub fn fetch_latest(local: &str) -> Result<Option<Release>, String> {
    let Some(kind) = package_kind() else {
        return Ok(None);
    };
    let url = format!("https://api.github.com/repos/{REPO}/releases/latest");
    let response = ureq::get(&url)
        .set("User-Agent", "fluxv2-updater")
        .set("Accept", "application/vnd.github+json")
        .call()
        .map_err(|err| err.to_string())?;
    let payload: serde_json::Value = response.into_json().map_err(|err| err.to_string())?;
    let version = payload
        .get("tag_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_start_matches(['v', 'V'])
        .to_string();
    if version.is_empty() || !is_newer(&version, local) {
        return Ok(None);
    }
    let wanted = asset_name(&version, &kind);
    let assets = payload.get("assets").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    for asset in assets {
        let name = asset.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if name != wanted {
            continue;
        }
        let url = asset.get("browser_download_url").and_then(|v| v.as_str()).unwrap_or("");
        let digest = asset
            .get("digest")
            .and_then(|v| v.as_str())
            .and_then(digest_hex);
        if !safe_url(url) {
            return Err("the release URL is not on an allowed host".into());
        }
        let Some(digest) = digest else {
            return Err("the release has no sha256 digest".into());
        };
        return Ok(Some(Release {
            version,
            url: url.to_string(),
            digest,
            kind,
        }));
    }
    Ok(None)
}

pub fn install(release: &Release) -> Result<(), String> {
    if !safe_url(&release.url) {
        return Err("the release URL is not on an allowed host".into());
    }
    let dir = std::env::temp_dir().join(format!("fluxv2-{}", std::process::id()));
    std::fs::create_dir_all(&dir).map_err(|err| err.to_string())?;
    let path = dir.join(asset_name(&release.version, &release.kind));
    download(&release.url, &path)?;
    if sha256_file(&path)? != release.digest {
        let _ = std::fs::remove_dir_all(&dir);
        return Err("the download digest did not match the release".into());
    }
    let result = match release.kind {
        PackageKind::Rpm => run_pkexec(&["dnf", "install", "-y", &path.to_string_lossy()]),
        PackageKind::Deb => run_pkexec(&["dpkg", "-i", &path.to_string_lossy()]),
        PackageKind::Flatpak => install_flatpak(&path),
    };
    let _ = std::fs::remove_dir_all(&dir);
    result
}

pub fn relaunch() -> Result<(), String> {
    let program = std::env::current_exe().map_err(|err| err.to_string())?;
    Command::new(program)
        .env("FLUXV2_RESUME", "1")
        .env_remove("FLUXV2_KEEP_TINT")
        .spawn()
        .map_err(|err| err.to_string())?;
    std::env::set_var("FLUXV2_KEEP_TINT", "1");
    Ok(())
}

fn download(url: &str, dest: &Path) -> Result<(), String> {
    let response = ureq::get(url)
        .set("User-Agent", "fluxv2-updater")
        .call()
        .map_err(|err| err.to_string())?;
    let mut reader = response.into_reader();
    let mut file = File::create(dest).map_err(|err| err.to_string())?;
    let mut buf = [0_u8; 64 * 1024];
    loop {
        let n = reader.read(&mut buf).map_err(|err| err.to_string())?;
        if n == 0 {
            break;
        }
        file.write_all(&buf[..n]).map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    use std::io::Read;
    let mut file = File::open(path).map_err(|err| err.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = [0_u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf).map_err(|err| err.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finish())
}

fn run_pkexec(args: &[&str]) -> Result<(), String> {
    let status = Command::new("pkexec")
        .args(args)
        .status()
        .map_err(|err| err.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("install failed ({status})"))
    }
}

fn install_flatpak(path: &Path) -> Result<(), String> {
    let status = Command::new("flatpak")
        .args(["install", "--user", "-y", &path.to_string_lossy()])
        .status()
        .map_err(|err| err.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err("flatpak install failed".into())
    }
}

struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    filled: usize,
    bits: u64,
}

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buffer: [0; 64],
            filled: 0,
            bits: 0,
        }
    }

    fn update(&mut self, data: &[u8]) {
        for &byte in data {
            self.buffer[self.filled] = byte;
            self.filled += 1;
            if self.filled == 64 {
                self.transform();
                self.filled = 0;
            }
        }
        self.bits = self.bits.wrapping_add((data.len() as u64).wrapping_mul(8));
    }

    fn finish(mut self) -> String {
        let bits = self.bits;
        self.buffer[self.filled] = 0x80;
        self.filled += 1;
        if self.filled > 56 {
            while self.filled < 64 {
                self.buffer[self.filled] = 0;
                self.filled += 1;
            }
            self.transform();
            self.filled = 0;
        }
        while self.filled < 56 {
            self.buffer[self.filled] = 0;
            self.filled += 1;
        }
        self.buffer[56..64].copy_from_slice(&bits.to_be_bytes());
        self.transform();
        self.state.iter().map(|word| format!("{word:08x}")).collect()
    }

    fn transform(&mut self) {
        const K: [u32; 64] = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2,
        ];
        let mut w = [0_u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(self.buffer[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut h = self.state;
        for i in 0..64 {
            let s1 = h[4].rotate_right(6) ^ h[4].rotate_right(11) ^ h[4].rotate_right(25);
            let ch = (h[4] & h[5]) ^ (!h[4] & h[6]);
            let t1 = h[7]
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = h[0].rotate_right(2) ^ h[0].rotate_right(13) ^ h[0].rotate_right(22);
            let maj = (h[0] & h[1]) ^ (h[0] & h[2]) ^ (h[1] & h[2]);
            let t2 = s0.wrapping_add(maj);
            h[7] = h[6];
            h[6] = h[5];
            h[5] = h[4];
            h[4] = h[3].wrapping_add(t1);
            h[3] = h[2];
            h[2] = h[1];
            h[1] = h[0];
            h[0] = t1.wrapping_add(t2);
        }
        for i in 0..8 {
            self.state[i] = self.state[i].wrapping_add(h[i]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_order_matches_the_python_updater() {
        assert!(is_newer("2.0.18", "2.0.9"));
        assert!(!is_newer("2.0.18", "2.0.18"));
        assert_eq!(parse_version("v2.0.18-deb1"), vec![2, 0, 18]);
    }

    #[test]
    fn urls_and_digests_are_strict() {
        assert!(safe_url("https://github.com/Bistor1/Flux_v2/releases/download/x"));
        assert!(safe_url("https://release-assets.githubusercontent.com/file"));
        assert!(!safe_url("http://github.com/Bistor1/file"));
        assert!(!safe_url("https://example.com/fluxv2.rpm"));
        assert_eq!(digest_hex("sha256:ab".repeat(1).as_str()).is_none(), true);
        let hex = "ab".repeat(32);
        assert_eq!(digest_hex(&format!("SHA256:{hex}")).as_deref(), Some(hex.as_str()));
    }

    #[test]
    fn fedora_asset_name_matches_the_rpm_we_build() {
        assert_eq!(
            asset_name("2.0.20", &PackageKind::Rpm),
            "fluxv2-2.0.20-1.fc43.x86_64.rpm"
        );
    }

    #[test]
    fn sha256_matches_the_empty_string() {
        let mut hasher = Sha256::new();
        hasher.update(b"");
        assert_eq!(
            hasher.finish(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
