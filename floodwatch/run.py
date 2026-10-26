"""Runs the whole pipeline: fetch -> clean -> locate -> store history -> trends -> analytics -> site data.

Usage:
    python -m floodwatch.run                     # live run against JPS
    python -m floodwatch.run --from-csv FILE     # offline run from a saved snapshot (testing)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

import pandas as pd

from . import analytics, history
from .clean import clean
from .config import MYT, RECENT_HOURS, SITE_DATA_DIR
from .locate import add_locations
from .scrape import fetch_all

log = logging.getLogger("floodwatch")

STATION_FIELDS = [
    "station_id", "station_name", "state", "district", "basin", "sub_basin",
    "lat", "lon", "location_source", "observed_at", "age_hours",
    "water_level", "normal", "alert", "warning", "danger",
    "status", "quality", "thresholds_ok", "danger_ratio",
    "rate_m_per_h", "trend", "rising_fast", "hours_to_danger",
]


def _json_default(o):
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if pd.isna(o):
        return None
    raise TypeError(type(o))


def _write_json(name: str, payload) -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = SITE_DATA_DIR / name
    path.write_text(json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _clean_records(df: pd.DataFrame) -> list[dict]:
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def recent_series(hist: pd.DataFrame) -> dict:
    """{station_id: [[unix_minutes, level], ...]} kept compact for the browser."""
    out = {}
    if hist.empty:
        return out
    hist = hist.sort_values("observed_at")
    # Minutes since 1970 (independent of pandas' internal time resolution)
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    minutes = ((hist["observed_at"] - epoch) // pd.Timedelta(minutes=1)).astype(int)
    for sid, idx in hist.groupby("station_id").groups.items():
        out[sid] = [[int(m), round(float(v), 2)] for m, v in zip(minutes[idx], hist.loc[idx, "water_level"])]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-csv", help="use a saved snapshot instead of fetching JPS")
    parser.add_argument("--now", help="override the run time (ISO 8601), for testing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(MYT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MYT)

    if args.from_csv:
        raw = pd.read_csv(args.from_csv, dtype=str, keep_default_na=False).to_dict(orient="records")
        failed = []
    else:
        raw, failed = fetch_all()

    if not raw:
        log.error("No stations fetched (failed: %s). Leaving existing data untouched.", failed)
        return 1

    df = add_locations(clean(raw, now=now))
    new_rows = history.append_readings(df)
    hist = history.load_recent(RECENT_HOURS, now=now)
    df = history.add_trends(df, history.rate_of_change(hist))
    history.log_run(df, failed, now)

    stations = df[STATION_FIELDS].copy()
    stations["observed_at"] = stations["observed_at"].map(lambda t: t.isoformat() if pd.notna(t) else None)

    ok = df["quality"].eq("OK")
    summary = {
        "generated_at": now.isoformat(),
        "stations": len(df),
        "reporting": int(ok.sum()),
        "offline": int(df["quality"].eq("OFFLINE").sum()),
        "stale": int(df["quality"].eq("STALE").sum()),
        "status_counts": {s: int((ok & df["status"].eq(s)).sum()) for s in ["NORMAL", "ALERT", "WARNING", "DANGER"]},
        "rising_fast": int((ok & df["rising_fast"].fillna(False)).sum()),
        "failed_states": failed,
        "new_readings": new_rows,
        "source": "Jabatan Pengairan dan Saliran Malaysia (JPS) Public Infobanjir",
    }

    _write_json("latest.json", {"summary": summary, "stations": _clean_records(stations)})
    _write_json("recent.json", {"generated_at": now.isoformat(), "series": recent_series(hist)})
    _write_json("analytics.json", {"generated_at": now.isoformat(), **analytics.build(df, now)})

    log.info("Stations %d | reporting %d | danger %d | warning %d | alert %d | new readings %d | failed %s",
             summary["stations"], summary["reporting"], summary["status_counts"]["DANGER"],
             summary["status_counts"]["WARNING"], summary["status_counts"]["ALERT"], new_rows, failed or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
