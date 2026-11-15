//! Redshift blackbody ramps. 6500K is the identity ramp, not "off".

include!("blackbody.rs");

pub const TABLE_MIN: i32 = 1000;
pub const TABLE_STEP: i32 = 100;

pub fn table_max() -> i32 {
    TABLE_MIN + TABLE_STEP * (BLACKBODY.len() as i32 - 1)
}

pub fn channel_factors(temp: i32) -> (f64, f64, f64) {
    let last = table_max();
    let clamped = temp.clamp(TABLE_MIN, last);
    let position = (clamped - TABLE_MIN) as f64 / TABLE_STEP as f64;
    let index = position as usize;
    if index >= BLACKBODY.len() - 1 {
        return BLACKBODY[BLACKBODY.len() - 1];
    }
    let blend = position - index as f64;
    let start = BLACKBODY[index];
    let end = BLACKBODY[index + 1];
    (
        (1.0 - blend) * start.0 + blend * end.0,
        (1.0 - blend) * start.1 + blend * end.1,
        (1.0 - blend) * start.2 + blend * end.2,
    )
}

pub fn fill_ramp_into(factor: f64, out: &mut [u16]) {
    let last = (out.len() - 1) as f64;
    for (index, slot) in out.iter_mut().enumerate() {
        let value = ((index as f64 / last) * factor * 65535.0).round() as i32;
        *slot = value.clamp(0, 65535) as u16;
    }
}

pub fn temperature_ramps(temp: i32, size: usize) -> (Vec<u16>, Vec<u16>, Vec<u16>) {
    assert!(size >= 2, "gamma ramp is too small");
    let (red, green, blue) = channel_factors(temp);
    let mut r = vec![0; size];
    let mut g = vec![0; size];
    let mut b = vec![0; size];
    fill_ramp_into(red, &mut r);
    fill_ramp_into(green, &mut g);
    fill_ramp_into(blue, &mut b);
    (r, g, b)
}

/// Little-endian red, green, blue. Reuses `out` so a slider tick does not allocate.
pub fn write_ramp_bytes(temp: i32, size: usize, out: &mut Vec<u8>) {
    assert!(size >= 2, "gamma ramp is too small");
    let (red, green, blue) = channel_factors(temp);
    out.clear();
    out.reserve(size * 6);
    push_channel(red, size, out);
    push_channel(green, size, out);
    push_channel(blue, size, out);
}

fn push_channel(factor: f64, size: usize, out: &mut Vec<u8>) {
    let last = (size - 1) as f64;
    for index in 0..size {
        let value = ((index as f64 / last) * factor * 65535.0).round() as i32;
        let value = value.clamp(0, 65535) as u16;
        out.extend_from_slice(&value.to_le_bytes());
    }
}

pub fn accent(temp: i32, min: i32, max: i32) -> (u8, u8, u8) {
    const STOPS: [(f64, (u8, u8, u8)); 4] = [
        (0.00, (239, 90, 20)),
        (0.40, (255, 176, 48)),
        (0.72, (255, 236, 210)),
        (1.00, (147, 197, 253)),
    ];
    let span = (max - min).max(1) as f64;
    let ratio = ((temp - min) as f64 / span).clamp(0.0, 1.0);
    for pair in STOPS.windows(2) {
        let (left, c0) = pair[0];
        let (right, c1) = pair[1];
        if ratio <= right {
            let u = (ratio - left) / (right - left);
            let mix = |a: u8, b: u8| (a as f64 + (b as f64 - a as f64) * u).round() as u8;
            return (mix(c0.0, c1.0), mix(c0.1, c1.1), mix(c0.2, c1.2));
        }
    }
    STOPS[STOPS.len() - 1].1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_covers_1000_to_10000() {
        assert_eq!(BLACKBODY.len(), 91);
        assert_eq!(table_max(), 10000);
    }

    #[test]
    fn daylight_is_identity() {
        assert_eq!(channel_factors(6500), (1.0, 1.0, 1.0));
    }

    #[test]
    fn clamp_matches_the_table() {
        let last = BLACKBODY[BLACKBODY.len() - 1];
        let first = BLACKBODY[0];
        assert_eq!(channel_factors(10000), last);
        assert_eq!(channel_factors(25100), last);
        assert_ne!(channel_factors(9950), last);
        assert_eq!(channel_factors(500), first);
        assert_eq!(channel_factors(-1), first);
    }

    #[test]
    fn interpolation_stays_between_neighbours() {
        let below = channel_factors(3000);
        let above = channel_factors(3100);
        let mid = channel_factors(3050);
        for (b, a, m) in [below.0, below.1, below.2]
            .into_iter()
            .zip([above.0, above.1, above.2])
            .zip([mid.0, mid.1, mid.2])
            .map(|((b, a), m)| (b, a, m))
        {
            assert!(m >= b.min(a) && m <= b.max(a));
        }
    }

    #[test]
    fn ramps_are_monotonic() {
        for temp in [1000, 2700, 3400, 5000, 6500] {
            for ramp in {
                let (r, g, b) = temperature_ramps(temp, 1024);
                [r, g, b]
            } {
                assert_eq!(ramp.len(), 1024);
                assert_eq!(ramp[0], 0);
                assert!(ramp.windows(2).all(|w| w[1] >= w[0]));
            }
        }
    }

    #[test]
    fn identity_bytes_are_three_equal_channels() {
        let mut data = Vec::new();
        write_ramp_bytes(6500, 256, &mut data);
        assert_eq!(data.len(), 256 * 6);
        assert_eq!(&data[..512], &data[512..1024]);
        assert_eq!(&data[512..1024], &data[1024..]);
    }
}
