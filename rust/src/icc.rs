//! sRGB profile plus a VCGT, written through liblcms2. Profiles are cached by temperature.

use std::ffi::CString;
use std::os::unix::fs::PermissionsExt;
use std::os::raw::{c_char, c_double, c_int, c_uint, c_void};
use std::path::Path;

use libloading::{Library, Symbol};

use crate::gamma::temperature_ramps;
use crate::paths::{data_dir, profile_path};

const VCGT: u32 = 0x7663_6774;

#[repr(C)]
struct XyY {
    x: c_double,
    y: c_double,
    y_big: c_double,
}

pub fn temperature_profile(temp: i32) -> Result<std::path::PathBuf, String> {
    let path = profile_path(temp);
    if path.is_file() {
        return Ok(path);
    }
    std::fs::create_dir_all(data_dir()).map_err(|err| err.to_string())?;
    write_profile(&path, temp)?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644));
    Ok(path)
}

pub fn prune_except(keep: &Path) {
    let Ok(dir) = std::fs::read_dir(data_dir()) else {
        return;
    };
    let keep = std::fs::canonicalize(keep).unwrap_or_else(|_| keep.to_path_buf());
    for entry in dir.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !(name.starts_with("temperature-") && name.ends_with(".icc")) {
            continue;
        }
        let path = entry.path();
        if path == keep {
            continue;
        }
        let _ = std::fs::remove_file(path);
    }
}

fn write_profile(path: &Path, temp: i32) -> Result<(), String> {
    let lib = unsafe { Library::new("liblcms2.so.2") }.map_err(|err| err.to_string())?;
    unsafe {
        let build_gamma: Symbol<unsafe extern "C" fn(*mut c_void, c_double) -> *mut c_void> =
            lib.get(b"cmsBuildGamma\0").map_err(|err| err.to_string())?;
        let create: Symbol<
            unsafe extern "C" fn(*const XyY, *const XyY, *const *mut c_void) -> *mut c_void,
        > = lib.get(b"cmsCreateRGBProfile\0").map_err(|err| err.to_string())?;
        let tabulated: Symbol<
            unsafe extern "C" fn(*mut c_void, c_uint, *const u16) -> *mut c_void,
        > = lib
            .get(b"cmsBuildTabulatedToneCurve16\0")
            .map_err(|err| err.to_string())?;
        let write_tag: Symbol<unsafe extern "C" fn(*mut c_void, u32, *const c_void) -> c_int> =
            lib.get(b"cmsWriteTag\0").map_err(|err| err.to_string())?;
        let save: Symbol<unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int> = lib
            .get(b"cmsSaveProfileToFile\0")
            .map_err(|err| err.to_string())?;
        let close: Symbol<unsafe extern "C" fn(*mut c_void)> =
            lib.get(b"cmsCloseProfile\0").map_err(|err| err.to_string())?;
        let free_curve: Symbol<unsafe extern "C" fn(*mut c_void)> =
            lib.get(b"cmsFreeToneCurve\0").map_err(|err| err.to_string())?;

        let gamma = build_gamma(std::ptr::null_mut(), 2.2);
        if gamma.is_null() {
            return Err("lcms could not build a gamma curve".into());
        }
        let transfer = [gamma, gamma, gamma];
        let white = XyY { x: 0.3127, y: 0.3290, y_big: 1.0 };
        let primaries = [
            XyY { x: 0.64, y: 0.33, y_big: 1.0 },
            XyY { x: 0.30, y: 0.60, y_big: 1.0 },
            XyY { x: 0.15, y: 0.06, y_big: 1.0 },
        ];
        let profile = create(&white, primaries.as_ptr(), transfer.as_ptr());
        if profile.is_null() {
            free_curve(gamma);
            return Err("lcms could not create a display profile".into());
        }
        let (red, green, blue) = temperature_ramps(temp, 1024);
        let mut curves = Vec::new();
        for ramp in [&red, &green, &blue] {
            let curve = tabulated(std::ptr::null_mut(), ramp.len() as c_uint, ramp.as_ptr());
            if curve.is_null() {
                close(profile);
                for curve in curves {
                    free_curve(curve);
                }
                free_curve(gamma);
                return Err("lcms could not build a gamma table".into());
            }
            curves.push(curve);
        }
        let vcgt = [curves[0], curves[1], curves[2]];
        let tagged = write_tag(profile, VCGT, vcgt.as_ptr().cast());
        let path_c = CString::new(path.to_string_lossy().as_bytes()).map_err(|err| err.to_string())?;
        let saved = tagged != 0 && save(profile, path_c.as_ptr()) != 0;
        close(profile);
        for curve in curves {
            free_curve(curve);
        }
        free_curve(gamma);
        if saved {
            Ok(())
        } else {
            Err("lcms could not save the profile".into())
        }
    }
}
