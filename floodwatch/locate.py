"""Adds map coordinates to stations.

The live JPS pages have no coordinates. They come from the data.gov.my
flood-warning API snapshot (frozen on 23 Feb 2024), matched by station name or ID.
Stations not in that snapshot are placed at their district's centre and marked
as approximate; if the district is unknown too, they get no map position.
"""
from __future__ import annotations

import re

import pandas as pd

from .config import REFERENCE_DIR


def _key(text: str) -> str:
    return re.sub(r"[^A-Z]", "", str(text).upper())


def add_locations(df: pd.DataFrame) -> pd.DataFrame:
    exact = pd.read_csv(REFERENCE_DIR / "station_locations.csv", dtype={"station_id": str})
    centroids = pd.read_csv(REFERENCE_DIR / "district_centroids.csv")
    centroids["key"] = centroids["state"].map(_key) + "|" + centroids["district"].map(_key)
    cent = centroids.set_index("key")[["lat", "lon"]]

    out = df.copy()
    # Duplicated IDs were suffixed (-2); look up the original ID
    base_id = out["station_id"].str.replace(r"-\d+$", "", regex=True)
    ex = exact.set_index("station_id")
    out["lat"] = base_id.map(ex["lat"])
    out["lon"] = base_id.map(ex["lon"])
    out["location_source"] = out["lat"].notna().map({True: "exact", False: ""})

    missing = out["lat"].isna()
    keys = out["state"].map(_key) + "|" + out["district"].map(_key)
    out.loc[missing, "lat"] = keys[missing].map(cent["lat"])
    out.loc[missing, "lon"] = keys[missing].map(cent["lon"])
    # Looser match: JPS and data.gov.my spell some districts differently
    # (e.g. "Daerah Barat Daya" vs "Barat Daya Pulau Pinang")
    for i in out.index[out["lat"].isna()]:
        st = _key(out.at[i, "state"])
        d = _key(out.at[i, "district"]).removeprefix("DAERAH")
        if len(d) < 4:
            continue
        cand = centroids[(centroids["state"] == st) & centroids["district"].map(lambda x: d in _key(x) or _key(x) in d)]
        if len(cand) == 1:
            out.at[i, "lat"], out.at[i, "lon"] = cand["lat"].iat[0], cand["lon"].iat[0]

    approx = missing & out["lat"].notna()
    out.loc[approx, "location_source"] = "district"

    # Small federal territories: JPS uses local zone names (e.g. "Segambut",
    # "Zone 1") that aren't districts, so fall back to the territory's centre.
    small = centroids[centroids["state"].str.startswith("WILAYAHPERSEKUTUAN")]
    terr = small.groupby("state")[["lat", "lon"]].mean()
    still = out["lat"].isna() & out["state"].map(_key).isin(terr.index)
    out.loc[still, "lat"] = out.loc[still, "state"].map(_key).map(terr["lat"])
    out.loc[still, "lon"] = out.loc[still, "state"].map(_key).map(terr["lon"])
    out.loc[still, "location_source"] = "district"
    out.loc[out["lat"].isna(), "location_source"] = "none"
    return out
