"""Color temperature as per-channel gamma ramps. No redshift, no Night Light."""

import array
import os
import sys
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_double,
    c_uint16,
    c_uint32,
    c_void_p,
)

class _CIExyY(Structure):
    _fields_ = [("x", c_double), ("y", c_double), ("Y", c_double)]


class _CIExyYTriple(Structure):
    _fields_ = [("Red", _CIExyY), ("Green", _CIExyY), ("Blue", _CIExyY)]


# Redshift's blackbody table, 1000K through 25100K in 100K steps.
# 6500K is exactly (1, 1, 1). The old Tanner Helland curve is not, and the
# CIE piecewise fit jumps at 2222K and 4000K, so the slider felt uneven.
_BLACKBODY = (
    (1.00000000, 0.18172716, 0.00000000),
    (1.00000000, 0.25503671, 0.00000000),
    (1.00000000, 0.30942099, 0.00000000),
    (1.00000000, 0.35357379, 0.00000000),
    (1.00000000, 0.39091524, 0.00000000),
    (1.00000000, 0.42322816, 0.00000000),
    (1.00000000, 0.45159884, 0.00000000),
    (1.00000000, 0.47675916, 0.00000000),
    (1.00000000, 0.49923747, 0.00000000),
    (1.00000000, 0.51943421, 0.00000000),
    (1.00000000, 0.54360078, 0.08679949),
    (1.00000000, 0.56618736, 0.14065513),
    (1.00000000, 0.58734976, 0.18362641),
    (1.00000000, 0.60724493, 0.22137978),
    (1.00000000, 0.62600248, 0.25591950),
    (1.00000000, 0.64373109, 0.28819679),
    (1.00000000, 0.66052319, 0.31873863),
    (1.00000000, 0.67645822, 0.34786758),
    (1.00000000, 0.69160518, 0.37579588),
    (1.00000000, 0.70602449, 0.40267128),
    (1.00000000, 0.71976951, 0.42860152),
    (1.00000000, 0.73288760, 0.45366838),
    (1.00000000, 0.74542112, 0.47793608),
    (1.00000000, 0.75740814, 0.50145662),
    (1.00000000, 0.76888303, 0.52427322),
    (1.00000000, 0.77987699, 0.54642268),
    (1.00000000, 0.79041843, 0.56793692),
    (1.00000000, 0.80053332, 0.58884417),
    (1.00000000, 0.81024551, 0.60916971),
    (1.00000000, 0.81957693, 0.62893653),
    (1.00000000, 0.82854786, 0.64816570),
    (1.00000000, 0.83717703, 0.66687674),
    (1.00000000, 0.84548188, 0.68508786),
    (1.00000000, 0.85347859, 0.70281616),
    (1.00000000, 0.86118227, 0.72007777),
    (1.00000000, 0.86860704, 0.73688797),
    (1.00000000, 0.87576611, 0.75326132),
    (1.00000000, 0.88267187, 0.76921169),
    (1.00000000, 0.88933596, 0.78475236),
    (1.00000000, 0.89576933, 0.79989606),
    (1.00000000, 0.90198230, 0.81465502),
    (1.00000000, 0.90963069, 0.82838210),
    (1.00000000, 0.91710889, 0.84190889),
    (1.00000000, 0.92441842, 0.85523742),
    (1.00000000, 0.93156127, 0.86836903),
    (1.00000000, 0.93853986, 0.88130458),
    (1.00000000, 0.94535695, 0.89404470),
    (1.00000000, 0.95201559, 0.90658983),
    (1.00000000, 0.95851906, 0.91894041),
    (1.00000000, 0.96487079, 0.93109690),
    (1.00000000, 0.97107439, 0.94305985),
    (1.00000000, 0.97713351, 0.95482993),
    (1.00000000, 0.98305189, 0.96640795),
    (1.00000000, 0.98883326, 0.97779486),
    (1.00000000, 0.99448139, 0.98899179),
    (1.00000000, 1.00000000, 1.00000000),
    (0.98947904, 0.99348723, 1.00000000),
    (0.97940448, 0.98722715, 1.00000000),
    (0.96975025, 0.98120637, 1.00000000),
    (0.96049223, 0.97541240, 1.00000000),
    (0.95160805, 0.96983355, 1.00000000),
    (0.94303638, 0.96443333, 1.00000000),
    (0.93480451, 0.95923080, 1.00000000),
    (0.92689056, 0.95421394, 1.00000000),
    (0.91927697, 0.94937330, 1.00000000),
    (0.91194747, 0.94470005, 1.00000000),
    (0.90488690, 0.94018594, 1.00000000),
    (0.89808115, 0.93582323, 1.00000000),
    (0.89151710, 0.93160469, 1.00000000),
    (0.88518247, 0.92752354, 1.00000000),
    (0.87906581, 0.92357340, 1.00000000),
    (0.87315640, 0.91974827, 1.00000000),
    (0.86744421, 0.91604254, 1.00000000),
    (0.86191983, 0.91245088, 1.00000000),
    (0.85657444, 0.90896831, 1.00000000),
    (0.85139976, 0.90559011, 1.00000000),
    (0.84638799, 0.90231183, 1.00000000),
    (0.84153180, 0.89912926, 1.00000000),
    (0.83682430, 0.89603843, 1.00000000),
    (0.83225897, 0.89303558, 1.00000000),
    (0.82782969, 0.89011714, 1.00000000),
    (0.82353066, 0.88727974, 1.00000000),
    (0.81935641, 0.88452017, 1.00000000),
    (0.81530175, 0.88183541, 1.00000000),
    (0.81136180, 0.87922257, 1.00000000),
    (0.80753191, 0.87667891, 1.00000000),
    (0.80380769, 0.87420182, 1.00000000),
    (0.80018497, 0.87178882, 1.00000000),
    (0.79665980, 0.86943756, 1.00000000),
    (0.79322843, 0.86714579, 1.00000000),
    (0.78988728, 0.86491137, 1.00000000),
)


def channel_factors(temp):
    """Redshift RGB multipliers. 6500K is (1, 1, 1). Same factors on every output."""
    clamped = max(1000.0, min(25100.0, float(temp)))
    position = (clamped - 1000.0) / 100.0
    index = int(position)
    if index >= len(_BLACKBODY) - 1:
        return _BLACKBODY[-1]
    blend = position - index
    start = _BLACKBODY[index]
    end = _BLACKBODY[index + 1]
    return tuple((1.0 - blend) * start[channel] + blend * end[channel] for channel in range(3))


def fill_ramp(factor, size):
    """Linear ramp scaled by a channel factor. Black stays black."""
    if size < 2:
        raise ValueError("gamma ramp is too small")
    last = size - 1
    ramp = []
    for index in range(size):
        value = int(round((index / last) * factor * 65535.0))
        if value < 0:
            value = 0
        elif value > 65535:
            value = 65535
        ramp.append(value)
    return ramp


def identity_ramps(size):
    ramp = fill_ramp(1.0, size)
    return ramp, list(ramp), list(ramp)


def temperature_ramps(temp, size):
    red, green, blue = channel_factors(temp)
    return fill_ramp(red, size), fill_ramp(green, size), fill_ramp(blue, size)


def ramp_bytes(temp, size):
    """Little-endian uint16 ramps: red, then green, then blue.

    This is the layout zwlr_gamma_control_v1.set_gamma expects. 6500K is
    the identity ramp, not a request to restore someone else's gamma.
    """
    red, green, blue = temperature_ramps(temp, size)
    data = array.array("H")
    data.extend(red)
    data.extend(green)
    data.extend(blue)
    if sys.byteorder != "little":
        data.byteswap()
    return data.tobytes()


def _lcms():
    return CDLL("liblcms2.so.2")


_VCGT = 0x76636774


def write_temperature_profile(path, temp):
    """Neutral sRGB profile plus Redshift's per-channel gamma table.

    The white point stays D65, so color management does not repaint hues
    differently on each window. The VCGT is the same multiply Redshift writes
    into the gamma ramp, on every output.
    """
    lcms = _lcms()
    lcms.cmsBuildGamma.argtypes = [c_void_p, c_double]
    lcms.cmsBuildGamma.restype = c_void_p
    lcms.cmsCreateRGBProfile.argtypes = [
        POINTER(_CIExyY),
        POINTER(_CIExyYTriple),
        POINTER(c_void_p),
    ]
    lcms.cmsCreateRGBProfile.restype = c_void_p
    lcms.cmsBuildTabulatedToneCurve16.argtypes = [c_void_p, c_uint32, POINTER(c_uint16)]
    lcms.cmsBuildTabulatedToneCurve16.restype = c_void_p
    lcms.cmsWriteTag.argtypes = [c_void_p, c_uint32, c_void_p]
    lcms.cmsWriteTag.restype = c_uint32
    lcms.cmsSaveProfileToFile.argtypes = [c_void_p, c_char_p]
    lcms.cmsSaveProfileToFile.restype = c_uint32
    lcms.cmsCloseProfile.argtypes = [c_void_p]
    lcms.cmsFreeToneCurve.argtypes = [c_void_p]

    white = _CIExyY(0.3127, 0.3290, 1.0)
    primaries = _CIExyYTriple(
        _CIExyY(0.64, 0.33, 1.0),
        _CIExyY(0.30, 0.60, 1.0),
        _CIExyY(0.15, 0.06, 1.0),
    )
    gamma = lcms.cmsBuildGamma(None, 2.2)
    transfer = (c_void_p * 3)(gamma, gamma, gamma)
    profile = lcms.cmsCreateRGBProfile(byref(white), byref(primaries), transfer)
    if not profile:
        lcms.cmsFreeToneCurve(gamma)
        raise OSError("lcms could not create a display profile")
    curves = []
    try:
        for values in temperature_ramps(temp, 1024):
            table = (c_uint16 * len(values))(*values)
            curve = lcms.cmsBuildTabulatedToneCurve16(None, len(values), table)
            if not curve:
                raise OSError("lcms could not build a gamma table")
            curves.append(curve)
        vcgt = (c_void_p * 3)(*curves)
        if not lcms.cmsWriteTag(profile, _VCGT, vcgt):
            raise OSError("lcms could not write the gamma table")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not lcms.cmsSaveProfileToFile(profile, os.fsencode(path)):
            raise OSError("lcms could not save the profile")
    finally:
        lcms.cmsCloseProfile(profile)
        for curve in curves:
            lcms.cmsFreeToneCurve(curve)
        lcms.cmsFreeToneCurve(gamma)
    os.chmod(path, 0o644)
    return path
