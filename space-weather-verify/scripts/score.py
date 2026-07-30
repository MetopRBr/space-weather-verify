"""
Join data/forecasts.csv (date_valid) to data/observed.csv (date) and score
each source on:
  - Geomagnetic category (exact G-scale bin match)
  - Flare category (exact match, plus a looser "not under-forecast" check)
  - Proton event (binary hit / miss / false alarm / correct rejection)

Writes data/scorecard.csv (one row per source, updated every run) and
prints a markdown summary table to stdout for a quick look / pasting into
a GitHub Actions job summary.

Run this after fetch_observed.py so the newest day is included; it's
cheap to just re-run over the whole history each time rather than track
incremental state.
"""
from __future__ import annotations
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from common import GEOMAG_RANK, FLARE_RANK  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
FORECASTS_CSV = os.path.join(DATA_DIR, "forecasts.csv")
OBSERVED_CSV = os.path.join(DATA_DIR, "observed.csv")
SCORECARD_CSV = os.path.join(DATA_DIR, "scorecard.csv")


def load_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_bool(v):
    if v in (None, "", "None"):
        return None
    return str(v).strip().lower() in ("true", "1", "yes")


def main():
    forecasts = load_csv(FORECASTS_CSV)
    observed = {row["date"]: row for row in load_csv(OBSERVED_CSV)}

    if not forecasts:
        print("No forecasts logged yet.")
        return
    if not observed:
        print("No observed data logged yet.")
        return

    stats = defaultdict(lambda: {
        "n": 0,
        "geomag_hit": 0, "geomag_scored": 0,
        "geomag_underforecast": 0,
        "flare_hit": 0, "flare_scored": 0,
        "flare_underforecast": 0,
        "proton_hit": 0, "proton_miss": 0,
        "proton_false_alarm": 0, "proton_correct_reject": 0,
    })

    for f in forecasts:
        obs = observed.get(f["date_valid"])
        if obs is None:
            continue  # no ground truth yet for that date
        src = f["source"]
        s = stats[src]
        s["n"] += 1

        # --- geomagnetic ---
        fc, oc = f.get("g_category"), obs.get("g_category")
        if fc and oc and fc in GEOMAG_RANK and oc in GEOMAG_RANK:
            s["geomag_scored"] += 1
            if fc == oc:
                s["geomag_hit"] += 1
            if GEOMAG_RANK[fc] < GEOMAG_RANK[oc]:
                s["geomag_underforecast"] += 1

        # --- flares ---
        ffc, foc = f.get("flare_category"), obs.get("flare_category")
        if ffc and foc and ffc in FLARE_RANK and foc in FLARE_RANK:
            s["flare_scored"] += 1
            if ffc == foc:
                s["flare_hit"] += 1
            if FLARE_RANK[ffc] < FLARE_RANK[foc]:
                s["flare_underforecast"] += 1

        # --- protons (binary contingency table) ---
        fp = to_bool(f.get("proton_expected"))
        op = to_bool(obs.get("proton_event_s1plus"))
        if fp is not None and op is not None:
            if fp and op:
                s["proton_hit"] += 1
            elif fp and not op:
                s["proton_false_alarm"] += 1
            elif not fp and op:
                s["proton_miss"] += 1
            else:
                s["proton_correct_reject"] += 1

    # write scorecard.csv
    fieldnames = ["source", "n_scored_days",
                  "geomag_accuracy", "geomag_underforecast_rate",
                  "flare_accuracy", "flare_underforecast_rate",
                  "proton_hits", "proton_misses",
                  "proton_false_alarms", "proton_correct_rejections"]
    rows = []
    for src, s in sorted(stats.items()):
        geomag_acc = s["geomag_hit"] / s["geomag_scored"] if s["geomag_scored"] else None
        geomag_under = s["geomag_underforecast"] / s["geomag_scored"] if s["geomag_scored"] else None
        flare_acc = s["flare_hit"] / s["flare_scored"] if s["flare_scored"] else None
        flare_under = s["flare_underforecast"] / s["flare_scored"] if s["flare_scored"] else None
        rows.append({
            "source": src,
            "n_scored_days": s["n"],
            "geomag_accuracy": round(geomag_acc, 3) if geomag_acc is not None else "",
            "geomag_underforecast_rate": round(geomag_under, 3) if geomag_under is not None else "",
            "flare_accuracy": round(flare_acc, 3) if flare_acc is not None else "",
            "flare_underforecast_rate": round(flare_under, 3) if flare_under is not None else "",
            "proton_hits": s["proton_hit"],
            "proton_misses": s["proton_miss"],
            "proton_false_alarms": s["proton_false_alarm"],
            "proton_correct_rejections": s["proton_correct_reject"],
        })

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCORECARD_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # markdown summary for stdout / GitHub Actions job summary
    print("| Source | Days | Geomag acc. | Geomag under-fc. | Flare acc. | "
          "Flare under-fc. | Proton hit/miss/FA/CR |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['source']} | {r['n_scored_days']} | {r['geomag_accuracy']} | "
              f"{r['geomag_underforecast_rate']} | {r['flare_accuracy']} | "
              f"{r['flare_underforecast_rate']} | "
              f"{r['proton_hits']}/{r['proton_misses']}/"
              f"{r['proton_false_alarms']}/{r['proton_correct_rejections']} |")


if __name__ == "__main__":
    main()
