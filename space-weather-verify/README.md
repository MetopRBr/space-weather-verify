# Space weather forecast verification

Daily-automated comparison of 1-day-ahead forecasts from NOAA SWPC, SIDC,
the UK Met Office, and GFZ Potsdam, scored against observed conditions
(NOAA GOES/Kp data) for Kp/geomagnetic activity, solar flares, and proton
flux.

## How it works

1. **`scripts/fetch_forecasts.py`** — runs once daily, pulls each site's
   forecast for *tomorrow* (UTC), normalizes it onto common categorical
   scales (G-scale for geomagnetic, flare letter class, S1+ proton
   yes/no), and appends a row per source to `data/forecasts.csv`.
2. **`scripts/fetch_observed.py`** — runs the same day, pulls *yesterday's*
   observed Kp/X-ray/proton data from NOAA's raw feeds (independent of
   NOAA's own forecast text) and appends a row to `data/observed.csv`.
3. **`scripts/score.py`** — joins the two on date, computes per-source
   accuracy (exact-category hit rate for Kp and flares, a full hit/miss/
   false-alarm/correct-rejection table for proton events), and writes
   `data/scorecard.csv`.
4. **`.github/workflows/collect.yml`** — runs all three daily via GitHub
   Actions and commits the updated CSVs back to the repo.

Because the forecast for "tomorrow" and the observation of "yesterday"
happen in the same daily run, each row in `forecasts.csv` only gets
scored once `observed.csv` catches up to that date — `score.py` handles
that automatically (unscored days are just skipped until then).

## Setup

```bash
git clone <this-repo>
cd space-weather-verify
python scripts/fetch_forecasts.py   # test locally
python scripts/fetch_observed.py
python scripts/score.py
```

Push to GitHub, then either rely on the native `schedule:` trigger, or —
given your Metop pipeline found GitHub's native scheduler unreliable
(multi-hour delays) — point an external cron-job.org job at the
`workflow_dispatch` REST endpoint instead:

```
POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/collect.yml/dispatches
Authorization: Bearer <PAT with repo scope>
Content-Type: application/json

{"ref": "main"}
```

## Known limitations (read before trusting the numbers)

- **GFZ** only forecasts Kp — no flare or proton products exist, so GFZ
  only ever appears in the geomagnetic scoring. Worse, GFZ's numeric
  SWIFT-ensemble Kp forecast is served from an *internal* GFZ path (see
  `github.com/GFZ/KpAlert`); the public site only exposes it as a chart
  image. `fetch_gfz()` logs the chart URL rather than inventing a number
  — if you want GFZ in the Kp comparison, read the median/quantile off
  the chart and backfill `kp_raw` in `data/forecasts.csv` by hand, or
  swap in GFZ's Kp *nowcast* JSON API (`kp.gfz.de`) if you decide
  same-day nowcast accuracy is an acceptable substitute for a true
  forecast comparison.
- **NOAA flare category** is derived from the R-scale (radio blackout)
  probabilities in the 3-Day Forecast text product, not a direct
  per-class (C/M/X) flare probability — R1-R2 tracks C/M-class activity
  and R3+ tracks X-class, but it's a proxy, not identical to SIDC's
  direct "C-class flares ≥50%" style output. Worth keeping in mind if
  NOAA's flare score looks systematically different from the others.
- **Met Office** forecasts are prose, not numbers. `fetch_metoffice()`
  does keyword extraction (looking for "G1", "M-class", "background
  levels", etc.) which is inherently approximate and the most likely
  parser to need a regex tweak if the Met Office changes their wording
  — check `data/forecasts.csv`'s `notes` column if a Met Office row
  looks wrong, and re-read the raw text before trusting the score.
- **SIDC scraping**: `sidc.be/LatestSWData/LatestSWData.php` explicitly
  disallows automated fetches in robots.txt; `index.php` (used here)
  did not appear to be blocked at the time of writing, but you should
  check `sidc.be/robots.txt` yourself and consider emailing SIDC before
  running this daily long-term — a polite heads-up costs nothing and
  they may point you at a proper data feed.
- **Ground truth is NOAA's own observation feeds** for all four sources,
  which keeps scoring independent of any single forecaster, but it does
  mean the comparison inherits NOAA's observational definitions (e.g.
  GOES X-ray class boundaries, the 10 pfu S1 threshold) even when
  scoring non-NOAA forecasts.
- Sample size matters — a few weeks will mostly reflect quiet-period
  performance where everyone scores well. Let it run through at least
  one active period (flare cluster or CME arrival) before drawing
  conclusions about which source is actually more skillful.

## Extending

- Add a 5th source by writing a `fetch_<name>()` function returning the
  same dict shape as the others, adding it to the `fetchers` list in
  `fetch_forecasts.py`.
- To score 2-day or 3-day-ahead forecasts too, parameterize
  `target_date` in `fetch_forecasts.py` and add a `lead_time_days`
  column so `score.py` can break results out by lead time.
