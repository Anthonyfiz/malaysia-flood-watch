"""Tests run against a sample of real JPS rows (tests/fixtures/jps_rows_sample.csv)."""
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from floodwatch import history
from floodwatch.clean import clean
from floodwatch.config import MYT
from floodwatch.locate import add_locations
from floodwatch.scrape import parse_state_page

FIXTURE = Path(__file__).parent / "fixtures" / "jps_rows_sample.csv"
NOW = datetime(2026, 10, 26, 9, 10, tzinfo=MYT)


@pytest.fixture
def raw():
    return pd.read_csv(FIXTURE, dtype=str, keep_default_na=False).to_dict(orient="records")


@pytest.fixture
def df(raw):
    return add_locations(clean(raw, now=NOW)).set_index("station_id", drop=False)


def test_parse_state_page_skips_header_and_filter_rows():
    html = """
    <table><tr><td>Negeri: <select><option>Perlis</option></select></td></tr></table>
    <table>
      <tr><th>Bil.</th><th>ID Stesen</th><th>Nama Stesen</th><th>Daerah</th><th>Lembangan</th>
          <th>Sub Lembangan</th><th>Kemaskini Terakhir</th><th>Aras Air (m)</th><th>Tahap Nilai Ambang</th></tr>
      <tr><th>Normal</th><th>Waspada</th><th>Amaran</th><th>Bahaya</th></tr>
      <tr><td>1</td><td>0010491WL</td><td>Sg. Gial di  Balik Guar</td><td>Padang Besar</td><td>Sungai Arau</td>
          <td></td><td>26/10/2026 09:00:00</td><td><a href="#">11.35</a></td>
          <td>12.50</td><td>13.50</td><td>14.00</td><td>15.00</td></tr>
    </table>"""
    rows = parse_state_page(html, "Perlis")
    assert len(rows) == 1
    assert rows[0]["station_id"] == "0010491WL"
    assert rows[0]["station_name"] == "Sg. Gial di Balik Guar"
    assert rows[0]["water_level"] == "11.35"
    assert rows[0]["state"] == "Perlis"


def test_parse_real_jps_page():
    html = (Path(__file__).parent / "fixtures" / "jps_perlis_page.html").read_text(encoding="utf-8")
    rows = parse_state_page(html, "Perlis")
    assert [r["station_id"] for r in rows] == ["0010491WL", "0010531WL", "0010551WL", "6502436"]
    assert rows[0]["water_level"] == "11.35"
    assert rows[3]["danger"] == "29.60"
    assert rows[2]["sub_basin"] == "Sg. Chuchoh"


def test_infilling_code_is_not_a_reading(raw):
    row = dict(raw[0], water_level="-8,888.00")
    out = clean([row], now=NOW).iloc[0]
    assert pd.isna(out["water_level"]) and out["quality"] == "OFFLINE"


def test_status_classification(df):
    assert df.loc["0020541WL", "status"] == "DANGER"   # 21.64 >= danger 21.60
    assert df.loc["KPDG0003", "status"] == "WARNING"   # 2.84 >= warning 2.70
    assert df.loc["0020501WL", "status"] == "ALERT"    # 4.31 >= alert 4.30
    assert df.loc["0010491WL", "status"] == "NORMAL"


def test_sensor_error_is_offline(df):
    row = df.loc["5008001"]
    assert pd.isna(row["water_level"])
    assert row["quality"] == "OFFLINE"
    assert row["status"] == "UNKNOWN"


def test_thousands_separator(df):
    assert df.loc["4514401", "water_level"] == pytest.approx(1273.71)


def test_placeholder_thresholds_flagged(df):
    row = df.loc["PSPS10F2"]  # danger 320 m on a river at 10 m
    assert not row["thresholds_ok"]
    assert row["quality"] == "BAD_THRESHOLDS"
    assert row["status"] == "UNKNOWN"


def test_inconsistent_normal_level_has_no_danger_ratio(df):
    # Normal level set above alert level (normal 19 m, alert 15 m): status still works,
    # but "how close to danger" can't be measured from a wrong baseline
    assert df.loc["CPKN0001", "thresholds_ok"]
    assert pd.isna(df.loc["CPKN0001", "danger_ratio"])
    assert pd.isna(df.loc["0730891WL", "danger_ratio"])  # normal 674 m, danger 68 m


def test_stale_and_future_timestamps(df):
    assert df.loc["1829478", "quality"] == "STALE"          # Johor, a month old
    assert df.loc["6467401", "quality"] == "BAD_TIMESTAMP"  # dated December 2026


def test_duplicate_station_ids_kept_apart(df):
    assert {"3118446", "3118446-2", "0000211WL", "0000211WL-2"} <= set(df.index)
    assert df.loc["3118446-2", "station_name"] == "BATU 20, HULU LANGAT"


def test_locations(df):
    assert df.loc["0020541WL", "location_source"] == "exact"
    assert df.loc["0020501WL", "location_source"] == "district"   # Pendang, Kedah
    assert df.loc["PDBD04F2", "location_source"] == "district"    # "Daerah Barat Daya"
    assert df.loc["0232131WL", "location_source"] == "exact"
    assert df.loc["TESTNOLOC", "location_source"] == "none"
    located = df[df["location_source"] != "none"]
    assert located["lat"].between(0.5, 7.5).all() and located["lon"].between(99, 120).all()


def test_history_trend_and_time_to_danger(tmp_path, monkeypatch, raw):
    """Simulate a river rising 0.2 m every 30 minutes over 4 runs."""
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
    base = [r for r in raw if r["station_id"] == "0010491WL"][0]  # level 11.35, danger 15.00
    for i in range(4):
        t = datetime(2026, 10, 26, 7, 30, tzinfo=MYT) + timedelta(minutes=30 * i)
        row = dict(base, last_update=t.strftime("%d/%m/%Y %H:%M:%S"), water_level=f"{11.35 + 0.2 * i:.2f}")
        df = add_locations(clean([row], now=t + timedelta(minutes=5)))
        assert history.append_readings(df) == 1
        assert history.append_readings(df) == 0  # same reading twice is not stored again

    hist = history.load_recent(48, now=t + timedelta(minutes=5))
    rates = history.rate_of_change(hist)
    assert rates.iloc[0]["rate_m_per_h"] == pytest.approx(0.4, abs=1e-6)

    out = history.add_trends(df, rates).iloc[0]
    assert out["trend"] == "RISING"
    assert out["rising_fast"]
    # (15.00 - 11.95) / 0.4 m/h = 7.6 hours
    assert out["hours_to_danger"] == pytest.approx(7.6, abs=0.05)


def test_recent_series_uses_real_minutes():
    from floodwatch.run import recent_series
    t = pd.Timestamp("2026-10-26 09:00", tz=MYT)
    hist = pd.DataFrame({"observed_at": [t, t + pd.Timedelta(minutes=30)], "station_id": ["A", "A"], "water_level": [1.0, 1.5]})
    pts = recent_series(hist)["A"]
    assert pts[1][0] - pts[0][0] == 30
    assert pd.Timestamp(pts[0][0] * 60, unit="s", tz="UTC") == t
