"""Fetches the JPS Public Infobanjir state pages and pulls out the water-level table rows."""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .config import JPS_STATE_URL, STATES, USER_AGENT

log = logging.getLogger(__name__)

COLUMNS = [
    "station_id", "station_name", "district", "basin", "sub_basin",
    "last_update", "water_level", "normal", "alert", "warning", "danger",
]


def parse_state_page(html: str, state: str) -> list[dict]:
    """Return one dict per station row found in a JPS state page.

    The page has several tables (filters, headers, legend). Data rows are the
    ones with exactly 12 cells whose first cell is the running number.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) != 12:
            continue
        first = cells[0].get_text(strip=True)
        if not re.fullmatch(r"\d+", first):
            continue
        values = [re.sub(r"\s+", " ", c.get_text()).strip() for c in cells[1:]]
        row = dict(zip(COLUMNS, values))
        row["state"] = state
        rows.append(row)
    return rows


def fetch_state(session: requests.Session, state: str, retries: int = 3) -> str:
    url = JPS_STATE_URL.format(state=quote(state))
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("Fetch failed for %s (attempt %d/%d): %s", state, attempt, retries, exc)
            if attempt == retries:
                raise
            time.sleep(5 * attempt)
    raise RuntimeError("unreachable")


def fetch_all(delay_seconds: float = 1.0) -> tuple[list[dict], list[str]]:
    """Fetch every state politely (one at a time). Returns (rows, failed_states)."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    rows, failed = [], []
    for state in STATES:
        try:
            html = fetch_state(session, state)
            state_rows = parse_state_page(html, state)
            log.info("%-35s %3d stations", state, len(state_rows))
            rows.extend(state_rows)
        except requests.RequestException:
            failed.append(state)
        time.sleep(delay_seconds)
    return rows, failed
