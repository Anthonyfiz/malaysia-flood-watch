"""Shared settings for the pipeline."""
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REFERENCE_DIR = DATA_DIR / "reference"
HISTORY_DIR = DATA_DIR / "history"
SITE_DATA_DIR = ROOT / "docs" / "data"

MYT = ZoneInfo("Asia/Kuala_Lumpur")

JPS_BASE_URL = "https://publicinfobanjir.water.gov.my"
JPS_STATE_URL = JPS_BASE_URL + "/waterleveldata/{state}"

# State names exactly as JPS uses them in its URLs
STATES = [
    "Perlis", "Kedah", "Pulau Pinang", "Perak",
    "Wilayah Persekutuan Kuala Lumpur", "Selangor", "Negeri Sembilan",
    "Melaka", "Johor", "Pahang", "Terengganu", "Kelantan", "Sabah",
    "Wilayah Persekutuan Labuan", "Sarawak",
]

USER_AGENT = (
    "MalaysiaFloodWatch/1.0 (student portfolio project; "
    "https://github.com/Anthonyfiz/malaysia-flood-watch)"
)

# JPS codes for "no measured reading": -9999 = bad data, -8888 = infilling (estimated)
SENSOR_CODES = (-9999.0, -8888.0)

# A reading older than this is treated as stale (sensor or feed not updating)
STALE_AFTER_HOURS = 6

# A rise faster than this (metres per hour) is flagged as "rising fast"
RISING_FAST_M_PER_H = 0.10

# How many hours of history the dashboard charts show
RECENT_HOURS = 48

# Status levels in order of severity. JPS Malay names in brackets.
STATUS_ORDER = ["NORMAL", "ALERT", "WARNING", "DANGER"]  # Normal, Waspada, Amaran, Bahaya
