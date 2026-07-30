"""
Fetch day-ahead (1-day) space weather forecasts from:
  - NOAA SWPC   (fully automated: numeric text product)
  - SIDC        (automated: scraped structured forecast box)
  - Met Office  (automated: scraped prose, best-effort keyword extraction)
  - GFZ Potsdam (Kp only; forecast is chart-image-only publicly, so we log
                 the chart URL for manual reading rather than fake a number)

Appends one row per source per run to data/forecasts.csv.
Run daily via GitHub Actions, ideally 07:00-08:00 UTC once all four sites
have issued their morning update.
"""
from __future__ import annotations
import csv
import datetime as dt
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    flare_text_to_category, geomag_text_to_category, proton_text_to_bool,
    today_utc, iso_date,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_CSV = os.path.join(DATA_DIR, "forecasts.csv")

HEADERS = [
    "date_issued", "date_valid", "source",
    "kp_raw", "g_category",
    "flare_class_raw", "flare_category", "flare_probability_pct",
    "proton_raw", "proton_expected", "proton_probability_pct",
    "notes",
]

UA = {"User-Agent": "space-weather-forecast-verification-bot/1.0 "
                     "(research use; contact: set-your-contact-email-here)"}


def _get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------
# NOAA SWPC — https://services.swpc.noaa.gov/text/3-day-forecast.txt
# ---------------------------------------------------------------------
def fetch_noaa(target_date: dt.date) -> dict:
    url = "https://services.swpc.noaa.gov/text/3-day-forecast.txt"
    text = _get(url)

    m = re.search(r":Issued:\s*(\d{4} \w{3} \d{2} \d{4} UTC)", text)
    issued = dt.datetime.strptime(m.group(1), "%Y %b %d %H%M UTC") if m else None

    # Header line lists the three forecast dates, e.g. "Jul 20-Jul 22 2026"
    date_hdr = re.search(r"for (\w{3} \d{2})-(\w{3} \d{2}) (\d{4})", text)
    year = int(date_hdr.group(3)) if date_hdr else target_date.year

    # Column headers inside the Kp table, e.g. "Jul 20       Jul 21       Jul 22"
    col_match = re.search(
        r"\n\s*(\w{3} \d{2})\s+(\w{3} \d{2})\s+(\w{3} \d{2})\s*\n", text)
    if not col_match:
        raise ValueError("Could not locate NOAA Kp table column headers")
    cols = [dt.datetime.strptime(f"{c} {year}", "%b %d %Y").date()
            for c in col_match.groups()]
    try:
        col_idx = cols.index(target_date)
    except ValueError:
        raise ValueError(f"NOAA forecast does not cover {target_date}; "
                          f"available columns: {cols}")

    # Kp breakdown: 8 rows of "HH-HHUT   v1   v2   v3   (Gn)?" repeated per col
    kp_values = []
    for line in text.splitlines():
        if re.match(r"\s*\d{2}-\d{2}UT", line):
            nums = re.findall(r"(\d+\.\d+)", line)
            if len(nums) >= 3:
                kp_values.append(float(nums[col_idx]))
    max_kp = max(kp_values) if kp_values else None

    # Section B: proton (S1 or greater) percentages
    proton_pct = None
    pm = re.search(r"S1 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", text)
    if pm:
        proton_pct = int(pm.groups()[col_idx])

    # Section C: radio blackout percentages, used as a flare-class proxy
    # (R1-R2 ~ tracks C/M-class flare activity, R3+ ~ tracks X-class)
    r12 = re.search(r"R1-R2\s+(\d+)%\s+(\d+)%\s+(\d+)%", text)
    r3 = re.search(r"R3 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", text)
    r12_pct = int(r12.groups()[col_idx]) if r12 else None
    r3_pct = int(r3.groups()[col_idx]) if r3 else None

    flare_category = None
    if r3_pct is not None and r3_pct >= 50:
        flare_category = "X"
    elif r12_pct is not None and r12_pct >= 50:
        flare_category = "M"
    elif r12_pct is not None and r12_pct > 0:
        flare_category = "C"

    from common import kp_to_category
    return {
        "date_issued": iso_date(issued.date()) if issued else "",
        "date_valid": iso_date(target_date),
        "source": "NOAA",
        "kp_raw": max_kp,
        "g_category": kp_to_category(max_kp),
        "flare_class_raw": f"R1-R2:{r12_pct}% R3+:{r3_pct}%",
        "flare_category": flare_category,
        "flare_probability_pct": r12_pct,
        "proton_raw": f"S1+:{proton_pct}%",
        "proton_expected": (proton_pct is not None and proton_pct >= 50),
        "proton_probability_pct": proton_pct,
        "notes": "flare_category derived from R-scale (radio blackout) as proxy, "
                 "not a direct per-class flare probability product",
    }


# ---------------------------------------------------------------------
# SIDC — https://www.sidc.be/index.php  (structured "Forecasts" box)
# ---------------------------------------------------------------------
def fetch_sidc(target_date: dt.date) -> dict:
    url = "https://www.sidc.be/index.php"
    html = _get(url)
    text = re.sub(r"<[^>]+>", "\n", html)  # crude tag strip

    block_match = re.search(
        r"Forecasts\s*(.*?)\s*(?:Solar Activity|URSIgram)", text, re.S)
    block = block_match.group(1) if block_match else ""

    flare_m = re.search(
        r"Flare:\s*([A-Za-z\-]+class[^\n(]*)\s*\(?([\u2264\u2265<>=]*\s*\d+)%?\)?",
        block)
    proton_m = re.search(r"Protons:\s*([^\n]+)", block)
    geomag_m = re.search(r"Geomagnetic:\s*([^\n]+(?:\n[^\n]*\))?)", block)

    flare_raw = flare_m.group(1).strip() if flare_m else None
    flare_prob = None
    if flare_m and flare_m.group(2):
        pm = re.search(r"(\d+)", flare_m.group(2))
        flare_prob = int(pm.group(1)) if pm else None

    proton_raw = proton_m.group(1).strip() if proton_m else None
    geomag_raw = geomag_m.group(1).strip() if geomag_m else None

    from common import flare_class_to_category
    return {
        "date_issued": iso_date(today_utc()),
        "date_valid": iso_date(target_date),
        "source": "SIDC",
        "kp_raw": None,
        "g_category": geomag_text_to_category(geomag_raw or ""),
        "flare_class_raw": flare_raw,
        "flare_category": flare_class_to_category(flare_raw or ""),
        "flare_probability_pct": flare_prob,
        "proton_raw": proton_raw,
        "proton_expected": proton_text_to_bool(proton_raw or ""),
        "proton_probability_pct": None,
        "notes": "scraped from sidc.be forecast box; verify against "
                 "sidc.be/robots.txt and terms of use before scheduling "
                 "frequent automated runs",
    }


# ---------------------------------------------------------------------
# Met Office — prose scrape, best-effort keyword extraction
# ---------------------------------------------------------------------
def fetch_metoffice(target_date: dt.date) -> dict:
    url = "https://weather.metoffice.gov.uk/specialist-forecasts/space-weather"
    html = _get(url)
    text = re.sub(r"<[^>]+>", "\n", html)

    headline_m = re.search(r"Space Weather Forecast Headline:([^\n]+)", text)
    summary_m = re.search(
        r"Four-Day Space Weather Forecast Summary\s*(.*?)\s*Issued at:",
        text, re.S)
    headline = headline_m.group(1).strip() if headline_m else ""
    summary = summary_m.group(1).strip() if summary_m else ""
    combined = f"{headline}\n{summary}"

    return {
        "date_issued": iso_date(today_utc()),
        "date_valid": iso_date(target_date),
        "source": "MetOffice",
        "kp_raw": None,
        "g_category": geomag_text_to_category(combined),
        "flare_class_raw": headline,
        "flare_category": flare_text_to_category(combined),
        "flare_probability_pct": None,
        "proton_raw": summary,
        "proton_expected": proton_text_to_bool(combined),
        "proton_probability_pct": None,
        "notes": "prose-parsed; headline covers ~Day 1, summary spans "
                 "Days 1-4 so this is an approximation — check combined "
                 "field manually if a score looks off, and expect this "
                 "parser to need occasional regex updates if Met Office "
                 "changes their wording",
    }


# ---------------------------------------------------------------------
# GFZ Potsdam — Kp forecast is chart-image only on the public site;
# the numeric SWIFT ensemble CSV lives on GFZ's internal network
# (see github.com/GFZ/KpAlert), so we log the chart URL for manual
# reading rather than inventing a number.
# ---------------------------------------------------------------------
def fetch_gfz(target_date: dt.date) -> dict:
    chart_url = "https://spaceweather.gfz.de/fileadmin/SW-Monitor/kp_swift_ensemble_LAST.png"
    return {
        "date_issued": iso_date(today_utc()),
        "date_valid": iso_date(target_date),
        "source": "GFZ",
        "kp_raw": None,
        "g_category": None,
        "flare_class_raw": None,
        "flare_category": None,
        "flare_probability_pct": None,
        "proton_raw": None,
        "proton_expected": None,
        "proton_probability_pct": None,
        "notes": f"GFZ does not forecast flares/protons; Kp forecast is "
                 f"chart-image-only on the public site (no public numeric "
                 f"feed), see {chart_url} — read the median/quantile values "
                 f"off the chart and fill in kp_raw manually if you want "
                 f"GFZ included in Kp scoring",
    }


def main():
    target_date = today_utc() + dt.timedelta(days=1)
    fetchers = [fetch_noaa, fetch_sidc, fetch_metoffice, fetch_gfz]
    rows = []
    for fn in fetchers:
        try:
            rows.append(fn(target_date))
        except Exception as exc:  # keep going even if one source fails
            rows.append({
                "date_issued": iso_date(today_utc()),
                "date_valid": iso_date(target_date),
                "source": fn.__name__.replace("fetch_", ""),
                "kp_raw": None, "g_category": None,
                "flare_class_raw": None, "flare_category": None,
                "flare_probability_pct": None,
                "proton_raw": None, "proton_expected": None,
                "proton_probability_pct": None,
                "notes": f"FETCH FAILED: {exc}",
            })

    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.isfile(OUT_CSV)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Logged {len(rows)} forecasts for {target_date}")


if __name__ == "__main__":
    main()
