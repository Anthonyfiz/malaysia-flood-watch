"""Stores every reading over time, and works out how fast each river is changing.

JPS only shows the latest reading, so this project builds its own history:
each run appends new readings to data/history/YYYY-MM-DD.csv (one file per day,
by observation date). A summary of every run goes to data/runs.csv.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import HISTORY_DIR, DATA_DIR, MYT, RISING_FAST_M_PER_H

HIST_COLS = ["observed_at", "station_id", "water_level"]


def _day_file(day) -> "Path":
    return HISTORY_DIR / f"{day:%Y-%m-%d}.csv"


def append_readings(df: pd.DataFrame) -> int:
    """Append readings not seen before. Returns how many new rows were written."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    keep = df["water_level"].notna() & df["observed_at"].notna() & df["quality"].ne("BAD_TIMESTAMP")
    new = df.loc[keep, ["observed_at", "station_id", "water_level"]].copy()
    if new.empty:
        return 0
    new["day"] = new["observed_at"].dt.date
    written = 0
    for day, part in new.groupby("day"):
        path = _day_file(day)
        part = part[HIST_COLS].copy()
        part["observed_at"] = part["observed_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        if path.exists():
            old = pd.read_csv(path, dtype={"station_id": str, "observed_at": str})
            seen = set(zip(old["observed_at"], old["station_id"]))
            part = part[[(t, s) not in seen for t, s in zip(part["observed_at"], part["station_id"])]]
            combined = pd.concat([old, part])
        else:
            combined = part
        if len(part):
            combined.sort_values(["observed_at", "station_id"]).to_csv(path, index=False)
            written += len(part)
    return written


def load_recent(hours: int, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(MYT)
    start = now - timedelta(hours=hours)
    frames = []
    day = start.date()
    while day <= now.date():
        path = _day_file(day)
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"station_id": str}))
        day += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=HIST_COLS)
    hist = pd.concat(frames, ignore_index=True)
    hist["observed_at"] = pd.to_datetime(hist["observed_at"], format="%Y-%m-%dT%H:%M:%S%z").dt.tz_convert(MYT)
    return hist[(hist["observed_at"] >= start) & (hist["observed_at"] <= now)]


def rate_of_change(hist: pd.DataFrame, window_hours: float = 3.0) -> pd.DataFrame:
    """Slope of a straight line through each station's last few hours of readings (m/h).
    Needs at least 3 readings spanning 45 minutes, otherwise no trend is given."""
    if hist.empty:
        return pd.DataFrame(columns=["station_id", "rate_m_per_h"])
    end = hist["observed_at"].max()
    recent = hist[hist["observed_at"] >= end - pd.Timedelta(hours=window_hours)]
    out = []
    for sid, g in recent.groupby("station_id"):
        if len(g) < 3:
            continue
        hours = (g["observed_at"] - g["observed_at"].min()).dt.total_seconds() / 3600
        if hours.max() < 0.75:
            continue
        slope = np.polyfit(hours, g["water_level"], 1)[0]
        out.append((sid, round(float(slope), 4)))
    return pd.DataFrame(out, columns=["station_id", "rate_m_per_h"])


def add_trends(df: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(rates, on="station_id", how="left")
    r = out["rate_m_per_h"]
    out["trend"] = np.select([r >= 0.02, r <= -0.02, r.notna()], ["RISING", "FALLING", "STEADY"], default="")
    out["rising_fast"] = r >= RISING_FAST_M_PER_H
    # Straight-line estimate of hours until danger level, if rising and not there yet
    can = (r > 0.005) & out["thresholds_ok"] & out["status"].isin(["NORMAL", "ALERT", "WARNING"]) & out["quality"].eq("OK")
    hrs = ((out["danger"] - out["water_level"]) / r).where(can)
    out["hours_to_danger"] = hrs.where(hrs <= 24).round(1)
    return out


def log_run(df: pd.DataFrame, failed_states: list[str], run_at: datetime) -> None:
    path = DATA_DIR / "runs.csv"
    ok = df["quality"].eq("OK")
    row = {
        "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stations": len(df),
        "ok": int(ok.sum()),
        "offline": int(df["quality"].eq("OFFLINE").sum()),
        "stale": int(df["quality"].eq("STALE").sum()),
        "bad_thresholds": int(df["quality"].eq("BAD_THRESHOLDS").sum()),
        "alert": int((ok & df["status"].eq("ALERT")).sum()),
        "warning": int((ok & df["status"].eq("WARNING")).sum()),
        "danger": int((ok & df["status"].eq("DANGER")).sum()),
        "failed_states": ";".join(failed_states),
    }
    new = pd.DataFrame([row])
    if path.exists():
        new = pd.concat([pd.read_csv(path, dtype={"failed_states": str}), new])
    new.to_csv(path, index=False)
