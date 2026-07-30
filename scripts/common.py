"""
Shared helpers: normalizing heterogeneous forecast/observation formats
onto common categorical scales so NOAA / GFZ / Met Office / SIDC can be
scored against each other and against observed conditions.

Kept deliberately simple (no external deps beyond stdlib) so it runs
in a bare GitHub Actions runner without extra setup.
"""
from __future__ import annotations
import re
import datetime as dt

# ---------------------------------------------------------------------
# Geomagnetic: Kp -> NOAA G-scale category
# ---------------------------------------------------------------------
# https://www.spaceweather.gov/noaa-scales-explanation
GEOMAG_BINS = [
    (9.0, "G5"),
    (8.0, "G4"),
    (7.0, "G3"),
    (6.0, "G2"),
    (5.0, "G1"),
    (4.0, "Active"),
    (3.0, "Unsettled"),
    (0.0, "Quiet"),
]


def kp_to_category(kp: float | None) -> str | None:
    if kp is None:
        return None
    for threshold, label in GEOMAG_BINS:
        if kp >= threshold:
            return label
    return "Quiet"


# Ordinal rank so categories can be compared / scored (higher = more active)
GEOMAG_RANK = {"Quiet": 0, "Unsettled": 1, "Active": 2, "G1": 3, "G2": 4,
               "G3": 5, "G4": 6, "G5": 7}


def geomag_text_to_category(text: str) -> str | None:
    """Best-effort mapping from prose (Met Office / SIDC) to a G-scale bin."""
    if not text:
        return None
    t = text.lower()
    # check most severe first
    for level in ["g5", "extreme storm"]:
        if level in t:
            return "G5"
    for level in ["g4", "severe storm"]:
        if level in t:
            return "G4"
    for level in ["g3", "strong storm"]:
        if level in t:
            return "G3"
    for level in ["g2", "moderate storm"]:
        if level in t:
            return "G2"
    for level in ["g1", "minor storm"]:
        if level in t:
            return "G1"
    if "active" in t:
        return "Active"
    if "unsettled" in t:
        return "Unsettled"
    if "quiet" in t:
        return "Quiet"
    return None


# ---------------------------------------------------------------------
# Flares: bin by highest class letter (A/B < C < M < X)
# ---------------------------------------------------------------------
FLARE_RANK = {"None": 0, "A": 0, "B": 0, "C": 1, "M": 2, "X": 3}


def flare_class_to_category(label: str | None) -> str | None:
    """Collapse a raw flare class like 'M2.3' or 'X1' to its letter bin."""
    if not label:
        return None
    m = re.match(r"\s*([ABCMX])", label.strip().upper())
    if m:
        letter = m.group(1)
        return "C" if letter == "B" else letter if letter in ("C", "M", "X") else "None"
    return None


def xray_flux_to_class(flux_watts_per_m2: float) -> str:
    """Convert raw GOES long-channel (0.1-0.8nm) flux [W/m^2] to a flare
    class label, e.g. 3.2e-6 -> 'M3.2'. Standard NOAA convention."""
    import math
    if flux_watts_per_m2 <= 0:
        return "A0.0"
    exp = math.floor(math.log10(flux_watts_per_m2))
    coeff = flux_watts_per_m2 / (10 ** exp)
    table = {-8: "A", -7: "A", -6: "B", -5: "C", -4: "M", -3: "X"}
    # A-class covers everything below 1e-7; clamp
    if exp < -7:
        letter = "A"
        coeff = flux_watts_per_m2 / 1e-8
    elif exp > -4:
        letter = "X"
        coeff = flux_watts_per_m2 / 1e-4
    else:
        letter = table.get(exp, "A")
    return f"{letter}{coeff:.1f}"


def flare_text_to_category(text: str) -> str | None:
    """Best-effort mapping from prose (Met Office) to expected max flare bin."""
    if not text:
        return None
    t = text.lower()
    if "x-class" in t or "x class" in t:
        return "X"
    if "m-class" in t or "m class" in t:
        return "M"
    if "c-class" in t or "c class" in t:
        return "C"
    return None


# ---------------------------------------------------------------------
# Proton events: binary, >=10 MeV flux crossing 10 pfu (S1 threshold)
# ---------------------------------------------------------------------
def proton_text_to_bool(text: str) -> bool | None:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ["s1", "s2", "s3", "s4", "s5", "radiation storm", "elevated"]):
        if "no s1" in t or "not expected" in t or "background" in t:
            return False
        return True
    if "background" in t or "quiet" in t:
        return False
    return None


def iso_date(d: dt.date) -> str:
    return d.isoformat()


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
