//! One Wayland connection for the life of the tint. The worker coalesces slider ticks.

use std::os::fd::{AsFd, FromRawFd, OwnedFd};

use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::wl_output::WlOutput;
use wayland_client::protocol::wl_registry::{self, WlRegistry};
use wayland_client::{event_created_child, Connection, Dispatch, EventQueue, Proxy, QueueHandle};
use wayland_protocols_wlr::gamma_control::v1::client::{
    zwlr_gamma_control_manager_v1::ZwlrGammaControlManagerV1,
    zwlr_gamma_control_v1::{self, ZwlrGammaControlV1},
};

use crate::gamma::write_ramp_bytes;
use crate::icc::{prune_except, temperature_profile};
use crate::kde::{
    kde_output_configuration_v2::{self, ColorProfileSource, KdeOutputConfigurationV2},
    kde_output_device_mode_v2::KdeOutputDeviceModeV2,
    kde_output_device_registry_v2::{self, KdeOutputDeviceRegistryV2},
    kde_output_device_v2::{self, KdeOutputDeviceV2},
    kde_output_management_v2::KdeOutputManagementV2,
};
use crate::state::{self, DisplayState, OutputState};

const SOURCE_SRGB: u32 = 0;
const SOURCE_ICC: u32 = 1;

fn profile_source(value: u32) -> ColorProfileSource {
    match value {
        1 => ColorProfileSource::ICC,
        2 => ColorProfileSource::EDID,
        _ => ColorProfileSource::SRGB,
    }
}
const ICC_MIN: u32 = 8;
const HDR_MIN: u32 = 21;

struct GammaOut {
    proxy: ZwlrGammaControlV1,
    output: WlOutput,
    size: u32,
    failed: bool,
}

struct Device {
    proxy: KdeOutputDeviceV2,
    name: String,
    uuid: String,
    enabled: i32,
    icc: String,
    source: u32,
    hdr: u32,
    hdr_icc: String,
    hdr_source: u32,
    removed: bool,
}

struct State {
    outputs: Vec<WlOutput>,
    gamma: Vec<GammaOut>,
    devices: Vec<Device>,
    failure: String,
}

pub struct WaylandSession {
    conn: Connection,
    queue: EventQueue<State>,
    state: State,
    kind: &'static str,
    gamma_mgr: Option<ZwlrGammaControlManagerV1>,
    management: Option<KdeOutputManagementV2>,
    _registry: Option<KdeOutputDeviceRegistryV2>,
    broken: bool,
    ramp: Vec<u8>,
}

impl WaylandSession {
    pub fn open() -> Result<Self, String> {
        let conn = Connection::connect_to_env().map_err(|err| err.to_string())?;
        let (globals, queue) = registry_queue_init::<State>(&conn).map_err(|err| err.to_string())?;
        let qh = queue.handle();
        let mut outputs = Vec::new();
        for global in globals.contents().clone_list() {
            if global.interface == "wl_output" {
                outputs.push(globals.registry().bind(global.name, global.version.min(4), &qh, ()));
            }
        }
        let gamma_mgr = globals.bind(&qh, 1..=1, ()).ok();
        let management = globals.bind::<KdeOutputManagementV2, _, _>(&qh, 8..=22, ()).ok();
        let registry = globals.bind::<KdeOutputDeviceRegistryV2, _, _>(&qh, 21..=26, ()).ok();
        let mut session = Self {
            conn,
            queue,
            state: State {
                outputs,
                gamma: Vec::new(),
                devices: Vec::new(),
                failure: String::new(),
            },
            kind: "",
            gamma_mgr,
            management,
            _registry: registry,
            broken: false,
            ramp: Vec::with_capacity(1024 * 6),
        };
        session.roundtrip()?;
        session.choose_kind()?;
        Ok(session)
    }

    pub fn describe(&self) -> String {
        match self.kind {
            "wlr" => format!("wlroots gamma-control ({} outputs)", self.state.outputs.len()),
            "icc" => format!("KDE color profile ({} outputs)", self.enabled_count()),
            _ => "Wayland".into(),
        }
    }

    pub fn apply(&mut self, temp: i32) -> Result<String, String> {
        if self.broken {
            return Err("Wayland connection lost".into());
        }
        self.roundtrip()?;
        match self.kind {
            "wlr" => self.apply_wlr(temp),
            "icc" => self.apply_icc(temp),
            _ => Err("no usable Wayland gamma protocol".into()),
        }
    }

    pub fn restore(&mut self) -> Result<String, String> {
        if self.kind == "wlr" {
            for item in self.state.gamma.drain(..) {
                item.proxy.destroy();
            }
            let _ = self.conn.flush();
            state::clear_state();
            return Ok("restored".into());
        }
        self.restore_icc()
    }

    fn choose_kind(&mut self) -> Result<(), String> {
        if self.gamma_mgr.is_some() && !self.state.outputs.is_empty() {
            self.kind = "wlr";
            return Ok(());
        }
        let version = self.management.as_ref().map(|item| item.version()).unwrap_or(0);
        if self.management.is_some() && version >= ICC_MIN && !self.state.devices.is_empty() {
            self.kind = "icc";
            return Ok(());
        }
        if self.management.is_some() {
            return Err(format!(
                "KWin is too old to set a color profile (output management v{version}, need v{ICC_MIN})"
            ));
        }
        Err(
            "This Wayland compositor has no gamma protocol Flux can use. \
             wlroots gamma-control and KDE Plasma work. GNOME does not."
                .into(),
        )
    }

    fn roundtrip(&mut self) -> Result<(), String> {
        self.queue.roundtrip(&mut self.state).map(|_| ()).map_err(|_| {
            self.broken = true;
            "Wayland connection lost".to_string()
        })
    }

    fn apply_wlr(&mut self, temp: i32) -> Result<String, String> {
        self.ensure_gamma()?;
        let mut sent = 0usize;
        let mut fds = Vec::new();
        for item in &self.state.gamma {
            if item.failed || item.size < 2 {
                continue;
            }
            write_ramp_bytes(temp, item.size as usize, &mut self.ramp);
            let fd = memfd_with(&self.ramp)?;
            item.proxy.set_gamma(fd.as_fd());
            fds.push(fd);
            sent += 1;
        }
        self.conn.flush().map_err(|err| err.to_string())?;
        drop(fds);
        self.roundtrip()?;
        let applied = self.state.gamma.iter().filter(|item| !item.failed).count().min(sent);
        if applied == 0 {
            return Err("no output accepted gamma control".into());
        }
        state::write_state(&DisplayState {
            kind: "wlr".into(),
            temp: Some(temp),
            outputs: Vec::new(),
        });
        let total = self.state.gamma.len();
        let mut note = format!("wlroots · {temp}K · {applied} outputs");
        if applied < total {
            note.push_str(&format!(" ({applied} of {total})"));
        }
        Ok(note)
    }

    fn ensure_gamma(&mut self) -> Result<(), String> {
        let have: Vec<_> = self.state.gamma.iter().map(|item| item.output.id()).collect();
        let missing: Vec<WlOutput> = self
            .state
            .outputs
            .iter()
            .filter(|output| !have.contains(&output.id()))
            .cloned()
            .collect();
        if missing.is_empty() && self.state.gamma.iter().any(|item| !item.failed) {
            return Ok(());
        }
        let Some(mgr) = self.gamma_mgr.clone() else {
            return Err("this compositor has no wlroots gamma control".into());
        };
        let qh = self.queue.handle();
        for output in missing {
            let proxy = mgr.get_gamma_control(&output, &qh, ());
            self.state.gamma.push(GammaOut {
                proxy,
                output,
                size: 0,
                failed: false,
            });
        }
        self.roundtrip()?;
        self.state.gamma.retain(|item| item.size >= 2 && !item.failed);
        if self.state.gamma.is_empty() {
            return Err("no output accepted gamma control".into());
        }
        Ok(())
    }

    fn enabled_count(&self) -> usize {
        self.state.devices.iter().filter(|d| d.enabled != 0 && !d.removed).count()
    }

    fn apply_icc(&mut self, temp: i32) -> Result<String, String> {
        let path = temperature_profile(temp)?;
        let path_str = path.to_string_lossy().to_string();
        if state::read_state().map(|item| item.kind != "icc").unwrap_or(true) {
            state::write_state(&DisplayState {
                kind: "icc".into(),
                temp: None,
                outputs: snapshot(&self.state),
            });
        }
        let qh = self.queue.handle();
        let Some(management) = self.management.clone() else {
            return Err("KWin output management is missing".into());
        };
        let version = management.version();
        let config = management.create_configuration(&qh, ());
        let mut hdr = 0;
        for device in self.state.devices.iter().filter(|d| d.enabled != 0 && !d.removed) {
            config.set_icc_profile_path(&device.proxy, path_str.clone());
            config.set_color_profile_source(&device.proxy, profile_source(SOURCE_ICC));
            if device.hdr != 0 && version >= HDR_MIN {
                config.set_hdr_icc_profile_path(&device.proxy, path_str.clone());
                config.set_hdr_color_profile_source(&device.proxy, profile_source(SOURCE_ICC));
                hdr += 1;
            }
        }
        config.apply();
        self.roundtrip()?;
        if !self.state.failure.is_empty() {
            return Err(std::mem::take(&mut self.state.failure));
        }
        let mut file = state::read_state().unwrap_or_default();
        file.kind = "icc".into();
        file.temp = Some(temp);
        state::write_state(&file);
        prune_except(&path);
        let mut note = format!("KDE profile · {temp}K");
        if hdr > 0 {
            note.push_str(" · HDR profile set as well");
        }
        Ok(note)
    }

    fn restore_icc(&mut self) -> Result<String, String> {
        let saved = state::read_state();
        let qh = self.queue.handle();
        let Some(management) = self.management.clone() else {
            return Err("KWin output management is missing".into());
        };
        let config = management.create_configuration(&qh, ());
        let mut changed = false;
        for device in self.state.devices.iter().filter(|d| d.enabled != 0 && !d.removed) {
            if let Some(original) = saved.as_ref().and_then(|item| find_output(item, device)) {
                config.set_icc_profile_path(&device.proxy, original.icc.clone());
                config.set_color_profile_source(&device.proxy, profile_source(original.source));
                config.set_hdr_icc_profile_path(&device.proxy, original.hdr_icc.clone());
                config.set_hdr_color_profile_source(&device.proxy, profile_source(original.hdr_source));
                changed = true;
            } else if is_ours(&device.icc) || is_ours(&device.hdr_icc) {
                if is_ours(&device.icc) {
                    config.set_icc_profile_path(&device.proxy, String::new());
                    config.set_color_profile_source(&device.proxy, profile_source(SOURCE_SRGB));
                }
                if is_ours(&device.hdr_icc) {
                    config.set_hdr_icc_profile_path(&device.proxy, String::new());
                    config.set_hdr_color_profile_source(&device.proxy, profile_source(SOURCE_SRGB));
                }
                changed = true;
            }
        }
        if changed {
            config.apply();
            self.roundtrip()?;
        }
        state::clear_state();
        Ok("restored".into())
    }
}

fn snapshot(state: &State) -> Vec<OutputState> {
    state
        .devices
        .iter()
        .filter(|device| device.enabled != 0 && !device.removed)
        .map(|device| {
            let (icc, source) = ours_or(device.icc.clone(), device.source);
            let (hdr_icc, hdr_source) = ours_or(device.hdr_icc.clone(), device.hdr_source);
            OutputState {
                uuid: device.uuid.clone(),
                name: device.name.clone(),
                icc,
                source,
                hdr_icc,
                hdr_source,
                crtc: 0,
                red: Vec::new(),
                green: Vec::new(),
                blue: Vec::new(),
            }
        })
        .collect()
}

fn ours_or(path: String, source: u32) -> (String, u32) {
    if is_ours(&path) {
        (String::new(), SOURCE_SRGB)
    } else {
        (path, source)
    }
}

fn is_ours(path: &str) -> bool {
    path.contains("/fluxv2/") || path.contains("/fluxv2-rs/")
}

fn find_output<'a>(file: &'a DisplayState, device: &Device) -> Option<&'a OutputState> {
    file.outputs.iter().find(|item| {
        (!device.uuid.is_empty() && item.uuid == device.uuid)
            || (!device.name.is_empty() && item.name == device.name)
    })
}

fn memfd_with(bytes: &[u8]) -> Result<OwnedFd, String> {
    let raw = unsafe { libc::memfd_create(c"fluxv2-gamma".as_ptr(), libc::MFD_CLOEXEC) };
    if raw < 0 {
        return Err("memfd_create failed".into());
    }
    let mut file = unsafe { std::fs::File::from_raw_fd(raw) };
    use std::io::{Seek, SeekFrom, Write};
    file.write_all(bytes).map_err(|err| err.to_string())?;
    file.seek(SeekFrom::Start(0)).map_err(|err| err.to_string())?;
    Ok(file.into())
}

impl Dispatch<WlRegistry, GlobalListContents> for State {
    fn event(
        _: &mut Self,
        registry: &WlRegistry,
        event: wl_registry::Event,
        data: &GlobalListContents,
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        let _ = (registry, event, data, qh);
    }
}

impl Dispatch<WlOutput, ()> for State {
    fn event(_: &mut Self, _: &WlOutput, _: wayland_client::protocol::wl_output::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<ZwlrGammaControlManagerV1, ()> for State {
    fn event(
        _: &mut Self,
        _: &ZwlrGammaControlManagerV1,
        _: wayland_protocols_wlr::gamma_control::v1::client::zwlr_gamma_control_manager_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ZwlrGammaControlV1, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &ZwlrGammaControlV1,
        event: zwlr_gamma_control_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(item) = state.gamma.iter_mut().find(|item| item.proxy == *proxy) else {
            return;
        };
        match event {
            zwlr_gamma_control_v1::Event::GammaSize { size } => item.size = size,
            zwlr_gamma_control_v1::Event::Failed => item.failed = true,
            _ => {}
        }
    }
}

impl Dispatch<KdeOutputManagementV2, ()> for State {
    fn event(_: &mut Self, _: &KdeOutputManagementV2, _: crate::kde::kde_output_management_v2::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<KdeOutputDeviceRegistryV2, ()> for State {
    fn event(
        state: &mut Self,
        _: &KdeOutputDeviceRegistryV2,
        event: kde_output_device_registry_v2::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let kde_output_device_registry_v2::Event::Output { output } = event {
            state.devices.push(Device {
                proxy: output,
                name: String::new(),
                uuid: String::new(),
                enabled: 0,
                icc: String::new(),
                source: SOURCE_SRGB,
                hdr: 0,
                hdr_icc: String::new(),
                hdr_source: SOURCE_SRGB,
                removed: false,
            });
        }
    }

    event_created_child!(State, KdeOutputDeviceRegistryV2, [
        1 => (KdeOutputDeviceV2, ()),
    ]);
}

impl Dispatch<KdeOutputDeviceV2, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &KdeOutputDeviceV2,
        event: kde_output_device_v2::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(device) = state.devices.iter_mut().find(|item| item.proxy == *proxy) else {
            return;
        };
        match event {
            kde_output_device_v2::Event::Enabled { enabled } => device.enabled = enabled,
            kde_output_device_v2::Event::Uuid { uuid } => device.uuid = uuid,
            kde_output_device_v2::Event::Name { name } => device.name = name,
            kde_output_device_v2::Event::HighDynamicRange { hdr_enabled } => device.hdr = hdr_enabled,
            kde_output_device_v2::Event::IccProfilePath { profile_path } => device.icc = profile_path,
            kde_output_device_v2::Event::ColorProfileSource { source } => device.source = source.into(),
            kde_output_device_v2::Event::HdrIccProfilePath { profile_path } => device.hdr_icc = profile_path,
            kde_output_device_v2::Event::HdrColorProfileSource { source } => device.hdr_source = source.into(),
            kde_output_device_v2::Event::Removed => device.removed = true,
            _ => {}
        }
    }

    event_created_child!(State, KdeOutputDeviceV2, [
        2 => (KdeOutputDeviceModeV2, ()),
    ]);
}

impl Dispatch<KdeOutputDeviceModeV2, ()> for State {
    fn event(_: &mut Self, _: &KdeOutputDeviceModeV2, _: crate::kde::kde_output_device_mode_v2::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<KdeOutputConfigurationV2, ()> for State {
    fn event(
        state: &mut Self,
        _: &KdeOutputConfigurationV2,
        event: kde_output_configuration_v2::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let kde_output_configuration_v2::Event::FailureReason { reason } = event {
            state.failure = reason;
        }
    }
}
