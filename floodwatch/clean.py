"""Turns raw JPS rows into a clean, typed table with status and data-quality flags.

Data problems seen in the real feed (October 2026) and how they are handled:
  * "-9,999.00" (bad data) or "-8,888.00" (infilled estimate) -> quality = OFFLINE
  * numbers can contain thousands separators ("1,273.71") -> commas removed
  * placeholder thresholds such as 310 / 315 / 320 on a river at ~10 m,
    or thresholds out of order (alert > warning)          -> thresholds_ok = False
  * readings weeks or months old (whole states stop updating) -> quality = STALE
  * timestamps in the future                               -> quality = BAD_TIMESTAMP
  * the same station ID used for two different stations   -> made unique with a suffix
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import MYT, SENSOR_CODES, STALE_AFTER_HOURS

NUMERIC = ["water_level", "normal", "alert", "warning", "danger"]


def to_number(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace(",", "", regex=False).str.strip()
    cleaned = cleaned.replace("", pd.NA)
    return pd.to_numeric(cleaned, errors="coerce")


def make_ids_unique(df: pd.DataFrame) -> pd.DataFrame:
    """JPS occasionally reuses one station ID for two stations. Keep both by
    appending -2, -3 ... to later duplicates (stable because page order is stable)."""
    df = df.copy()
    n = df.groupby("station_id").cumcount()
    df["station_id"] = np.where(n == 0, df["station_id"], df["station_id"] + "-" + (n + 1).astype(str))
    return df


def thresholds_valid(df: pd.DataFrame) -> pd.Series:
    a, w, d, lvl = df["alert"], df["warning"], df["danger"], df["water_level"]
    has_all = a.notna() & w.notna() & d.notna()
    ordered = (a <= w) & (w <= d)
    # Placeholder thresholds: danger level more than 100 m above the current reading
    plausible = lvl.isna() | ((d - lvl) < 100)
    return has_all & ordered & plausible


def classify(df: pd.DataFrame) -> pd.Series:
    lvl = df["water_level"]
    status = pd.Series("NORMAL", index=df.index, dtype="object")
    status[lvl >= df["alert"]] = "ALERT"
    status[lvl >= df["warning"]] = "WARNING"
    status[lvl >= df["danger"]] = "DANGER"
    # No status if we can't trust the reading or the thresholds
    status[lvl.isna() | ~df["thresholds_ok"]] = "UNKNOWN"
    return status


def clean(raw_rows: list[dict], now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(MYT)
    df = pd.DataFrame(raw_rows)
    if df.empty:
        return df

    for col in ["station_id", "station_name", "district", "basin", "sub_basin", "state"]:
        df[col] = df[col].astype("string").str.strip().fillna("")
    df = make_ids_unique(df)

    for col in NUMERIC:
        df[col] = to_number(df[col])
    sensor_error = df["water_level"].round(0).isin(SENSOR_CODES)
    df.loc[sensor_error, "water_level"] = np.nan

    ts = pd.to_datetime(df["last_update"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    df["observed_at"] = ts.dt.tz_localize(MYT)
    age_h = (pd.Timestamp(now) - df["observed_at"]).dt.total_seconds() / 3600
    df["age_hours"] = age_h.round(2)

    df["thresholds_ok"] = thresholds_valid(df)
    df["status"] = classify(df)

    quality = pd.Series("OK", index=df.index, dtype="object")
    quality[~df["thresholds_ok"]] = "BAD_THRESHOLDS"
    quality[age_h > STALE_AFTER_HOURS] = "STALE"
    quality[df["observed_at"].isna() | (age_h < -1)] = "BAD_TIMESTAMP"
    quality[sensor_error | df["water_level"].isna()] = "OFFLINE"
    df["quality"] = quality

    # How far the river is between its normal level (0) and danger level (1)
    # Only meaningful if the normal level sits below the alert level
    normal_ok = df["normal"].notna() & (df["normal"] < df["alert"])
    span = (df["danger"] - df["normal"]).where(lambda s: s > 0)
    df["danger_ratio"] = ((df["water_level"] - df["normal"]) / span).where(df["thresholds_ok"] & normal_ok).round(3)

    return df.drop(columns=["last_update"])
