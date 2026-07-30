"""
Fetch OBSERVED conditions for the previous UTC day, used as ground truth
to score all four forecast sources against (not just NOAA's own forecast,
since NOAA's raw observation feeds are independent of NOAA's forecaster
text product).

Sources (all NOAA SWPC, since they're the standard reference feeds):
  - Kp:      https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
  - X-ray:   https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json
  - Protons: https://services.swpc.noaa.gov/products/alerts.json (S1+ alert text search)

Appends one row per day to data/observed.csv.
Run daily via GitHub Actions shortly after 00:00 UTC, to capture the
previous full UTC day.

NOTE: NOAA's JSON schemas occasionally change field names/shape. If a
parser here starts raising exceptions, fetch the URL directly and diff
the structure against what's assumed below before assuming the site is
down.
"""
from __future__ import annotations
import csv
import datetime as dt
import json
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from common import xray_flux_to_class, kp_to_category, iso_date, today_utc  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_CSV = os.path.join(DATA_DIR, "observed.csv")

HEADERS = [
    "date", "max_kp", "g_category",
    "max_xray_flux_wm2", "max_xray_class", "flare_category",
    "proton_event_s1plus",
]

UA = {"User-Agent": "space-weather-forecast-verification-bot/1.0 "
                     "(research use; contact: set-your-contact-email-here)"}


def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def observed_kp(target_date: dt.date) -> float | None:
    data = _get_json("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    # First row is typically a header, e.g. ["time_tag","Kp","a_running","station_count"]
    rows = data[1:] if data and isinstance(data[0], list) and not _is_num(data[0][1]) else data
    values = []
    for row in rows:
        try:
            ts = dt.datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if ts.date() == target_date:
                values.append(float(row[1]))
        except (ValueError, IndexError, TypeError):
            continue
    return max(values) if values else None


def _is_num(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def observed_max_xray(target_date: dt.date) -> tuple[float | None, str | None]:
    # 1-day feed comfortably covers "yesterday" as long as this job runs
    # early in the next UTC day.
    data = _get_json("https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json")
    fluxes = []
    for row in data:
        try:
            if row.get("energy") not in ("0.1-0.8nm", "0.1-0.8 nm"):
                continue
            ts = dt.datetime.fromisoformat(row["time_tag"].replace("Z", "+00:00"))
            if ts.date() == target_date:
                flux = float(row["flux"])
                if flux > 0 and not math.isnan(flux):
                    fluxes.append(flux)
        except (KeyError, ValueError, TypeError):
            continue
    if not fluxes:
        return None, None
    max_flux = max(fluxes)
    return max_flux, xray_flux_to_class(max_flux)


def observed_proton_event(target_date: dt.date) -> bool | None:
    try:
        alerts = _get_json("https://services.swpc.noaa.gov/products/alerts.json")
    except Exception:
        return None
    for alert in alerts:
        msg = (alert.get("message") or "")
        issue = alert.get("issue_datetime") or alert.get("issue_time") or ""
        try:
            ts = dt.datetime.fromisoformat(issue.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.date() != target_date:
            continue
        upper = msg.upper()
        if "RADIATION STORM" in upper or "10PFU" in upper or " S1" in upper \
                or " S2" in upper or " S3" in upper:
            if "WARNING" in upper or "ALERT" in upper or "EXCEEDED" in upper:
                return True
    return False  # no matching alert found for that date -> treat as no event


def main():
    target_date = today_utc() - dt.timedelta(days=1)

    max_kp = None
    max_flux = None
    max_class = None
    proton_event = None

    try:
        max_kp = observed_kp(target_date)
    except Exception as exc:
        print(f"WARNING: Kp fetch failed: {exc}", file=sys.stderr)

    try:
        max_flux, max_class = observed_max_xray(target_date)
    except Exception as exc:
        print(f"WARNING: X-ray fetch failed: {exc}", file=sys.stderr)

    try:
        proton_event = observed_proton_event(target_date)
    except Exception as exc:
        print(f"WARNING: proton alert fetch failed: {exc}", file=sys.stderr)

    from common import flare_class_to_category
    row = {
        "date": iso_date(target_date),
        "max_kp": max_kp,
        "g_category": kp_to_category(max_kp),
        "max_xray_flux_wm2": max_flux,
        "max_xray_class": max_class,
        "flare_category": flare_class_to_category(max_class) if max_class else None,
        "proton_event_s1plus": proton_event,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.isfile(OUT_CSV)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"Logged observed conditions for {target_date}: {row}")


if __name__ == "__main__":
    main()
