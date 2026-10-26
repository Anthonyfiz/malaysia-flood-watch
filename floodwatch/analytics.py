"""SQL analytics (DuckDB) over the latest snapshot, the reading history and the run log."""
from __future__ import annotations

import duckdb
import pandas as pd

from .config import DATA_DIR, HISTORY_DIR


def _records(con, sql: str) -> list[dict]:
    df = con.execute(sql).df()
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def build(latest: pd.DataFrame, now) -> dict:
    """`now` is the run time, so results are reproducible for a given run."""
    con = duckdb.connect()
    NOW = f"TIMESTAMPTZ '{now.isoformat()}'"
    con.register("latest", latest)

    history_glob = str(HISTORY_DIR / "*.csv")
    has_history = any(HISTORY_DIR.glob("*.csv"))
    if has_history:
        con.execute(f"""
            CREATE VIEW history AS
            SELECT CAST(observed_at AS TIMESTAMPTZ) AS observed_at,
                   CAST(station_id AS VARCHAR) AS station_id,
                   CAST(water_level AS DOUBLE) AS water_level
            FROM read_csv('{history_glob}', header = true, union_by_name = true,
                          columns = {{'observed_at': 'VARCHAR', 'station_id': 'VARCHAR', 'water_level': 'DOUBLE'}})
        """)

    out = {}

    # 1. Status and data quality per state, from the latest run
    out["by_state"] = _records(con, """
        SELECT state,
               COUNT(*)                                             AS stations,
               COUNT(*) FILTER (quality = 'OK')                     AS reporting,
               COUNT(*) FILTER (quality = 'OFFLINE')                AS offline,
               COUNT(*) FILTER (quality = 'STALE')                  AS stale,
               COUNT(*) FILTER (quality = 'BAD_THRESHOLDS')         AS bad_thresholds,
               COUNT(*) FILTER (quality = 'OK' AND status = 'ALERT')   AS alert,
               COUNT(*) FILTER (quality = 'OK' AND status = 'WARNING') AS warning,
               COUNT(*) FILTER (quality = 'OK' AND status = 'DANGER')  AS danger,
               ROUND(100.0 * COUNT(*) FILTER (quality = 'OK') / COUNT(*), 1) AS pct_reporting,
               MAX(observed_at) FILTER (quality <> 'BAD_TIMESTAMP') AS latest_reading
        FROM latest
        GROUP BY state
        ORDER BY danger DESC, warning DESC, alert DESC, state
    """)

    # 2. Stations closest to danger level right now (0 = normal, 1 = danger)
    out["closest_to_danger"] = _records(con, """
        SELECT station_id, station_name, state, district, water_level, danger, status,
               danger_ratio, rate_m_per_h
        FROM latest
        WHERE quality = 'OK' AND danger_ratio IS NOT NULL
        ORDER BY danger_ratio DESC
        LIMIT 10
    """)

    # 3. Data quality breakdown nationally
    out["quality_summary"] = _records(con, """
        SELECT quality, COUNT(*) AS stations
        FROM latest GROUP BY quality ORDER BY stations DESC
    """)

    if has_history:
        # 4. Stations most often at or above alert level in the last 7 days
        out["most_alerts_7d"] = _records(con, f"""
            WITH h AS (
                SELECT h.station_id, h.observed_at, h.water_level, l.alert
                FROM history h JOIN latest l USING (station_id)
                WHERE l.thresholds_ok AND h.observed_at >= {NOW} - INTERVAL 7 DAY
            )
            SELECT h.station_id, any_value(l.station_name) AS station_name, any_value(l.state) AS state,
                   COUNT(*) FILTER (h.water_level >= h.alert) AS readings_at_alert_or_above,
                   COUNT(*) AS readings,
                   ROUND(100.0 * COUNT(*) FILTER (h.water_level >= h.alert) / COUNT(*), 1) AS pct_time_alert
            FROM h JOIN latest l USING (station_id)
            GROUP BY h.station_id
            HAVING COUNT(*) FILTER (h.water_level >= h.alert) > 0
            ORDER BY readings_at_alert_or_above DESC
            LIMIT 10
        """)

        # 5. Biggest rise in the last 24 hours (latest reading minus lowest reading)
        out["biggest_rise_24h"] = _records(con, f"""
            WITH w AS (
                SELECT station_id, water_level, observed_at,
                       ROW_NUMBER() OVER (PARTITION BY station_id ORDER BY observed_at DESC) AS rn
                FROM history WHERE observed_at >= {NOW} - INTERVAL 24 HOUR
            ),
            s AS (
                SELECT station_id,
                       MAX(water_level) FILTER (rn = 1) - MIN(water_level) AS rise_m,
                       COUNT(*) AS readings
                FROM w GROUP BY station_id
            )
            SELECT s.station_id, l.station_name, l.state, ROUND(s.rise_m, 2) AS rise_m, s.readings
            FROM s JOIN latest l USING (station_id)
            WHERE l.quality = 'OK' AND s.readings >= 4 AND s.rise_m > 0
            ORDER BY s.rise_m DESC
            LIMIT 10
        """)

    # 6. National status over time, from the run log
    runs = DATA_DIR / "runs.csv"
    if runs.exists():
        out["national_trend"] = _records(con, f"""
            SELECT strftime(CAST(run_at AS TIMESTAMPTZ) AT TIME ZONE 'Asia/Kuala_Lumpur', '%Y-%m-%dT%H:%M') AS run_at,
                   stations, ok, offline, stale, alert, warning, danger
            FROM read_csv('{runs}', header = true)
            WHERE CAST(run_at AS TIMESTAMPTZ) >= {NOW} - INTERVAL 14 DAY
            ORDER BY run_at
        """)
    return out
