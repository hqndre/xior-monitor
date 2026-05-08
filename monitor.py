#!/usr/bin/env python3
"""
Xior Groningen studio availability monitor (v3 — xiorstudenthousing.eu edition).

Monitors the public Xior pages for the long-stay residences in Groningen.
These pages are NOT Cloudflare-protected, so we can fetch them directly with
plain HTTP requests — no Scrape.do, no JS rendering, no credit budget needed.

Strategy:
- For each property, fetch the page and extract:
  * Specific room numbers shown (pattern "# X-XXX" like "# 2-075")
  * A hash of the booking-relevant section (catches generic availability changes)
- Compare with previous state. Email if anything new appears.

State is kept in state.json (committed back by the workflow).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ----- properties to watch (all long-stay, Groningen) -----
PROPERTIES = {
    "Eendrachtskade": "https://www.xiorstudenthousing.eu/netherlands/groningen/eendrachtskade-student-accommodation/",
    "Oosterhamrikkade": "https://www.xiorstudenthousing.eu/netherlands/groningen/oosterhamrikkade-student-accommodation/",
    "Zernike Tower (long-stay)": "https://www.xiorstudenthousing.eu/netherlands/groningen/zernike-tower-student-accommodation/",
}

STATE_FILE = Path(__file__).parent / "state.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pattern for specific room numbers (e.g. "# 2-075", "# 12-3", "#5-100")
ROOM_NUMBER_RE = re.compile(r"#\s*\d{1,3}-\d{1,4}")


def fetch_url(url: str, retries: int = 2) -> str | None:
    """Fetch a URL with retries. Returns HTML on success, None on failure."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    for attempt in range(1, retries + 2):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.text
            print(f"  attempt {attempt}: HTTP {resp.status_code}")
        except requests.RequestException as e:
            print(f"  attempt {attempt}: network error: {e}")
        if attempt <= retries:
            time.sleep(10)
    return None


def extract_signature(html: str) -> dict:
    """Extract availability signals from the HTML."""
    soup = BeautifulSoup(html, "html.parser")
    # Use only the visible text — strip out scripts, styles, etc.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())

    # 1) Specific room numbers (strongest signal)
    rooms = sorted(set(ROOM_NUMBER_RE.findall(text)))

    # 2) Hash of the body text — catches any change to the page content.
    # Strip volatile bits (times like "10:13") to reduce false positives.
    cleaned = re.sub(r"\d{1,2}:\d{2}", "", text)
    page_hash = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]

    return {"rooms": rooms, "hash": page_hash}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            print(f"warn: could not parse state.json ({e}); starting fresh")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def send_email(subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_emails_raw = os.environ.get("TO_EMAIL", smtp_user)
    # Support multiple recipients separated by comma
    to_emails = [e.strip() for e in to_emails_raw.split(",") if e.strip()]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, to_emails, msg.as_string())
    print(f"email sent to {to_emails}: {subject}")


def check_once() -> None:
    """Run one check cycle: fetch all properties, compare to previous state, email if changed."""
    state = load_state()
    new_state: dict = {}
    alerts: list[str] = []

    for name, url in PROPERTIES.items():
        print(f"checking {name}...")
        html = fetch_url(url)
        if html is None:
            print(f"  failed to fetch — keeping previous state")
            if name in state:
                new_state[name] = state[name]
            continue

        sig = extract_signature(html)
        new_state[name] = sig
        print(f"  rooms={sig['rooms']} hash={sig['hash']}")

        prev = state.get(name)
        if prev is None:
            print(f"  first run for this property — recording baseline, no alert")
            continue

        prev_rooms = set(prev.get("rooms", []))
        new_rooms = [r for r in sig["rooms"] if r not in prev_rooms]
        hash_changed = prev.get("hash") != sig["hash"]

        if new_rooms:
            alerts.append(
                f"{name}: NEW SPECIFIC ROOMS available!\n"
                f"   Rooms: {', '.join(new_rooms)}\n"
                f"   Page: {url}\n"
                f"   Book now: https://www.xior-booking.com/"
            )
        elif hash_changed:
            alerts.append(
                f"{name}: page content changed (could be new availability)\n"
                f"   Check: {url}\n"
                f"   Book: https://www.xior-booking.com/"
            )

    if alerts:
        body = (
            "Xior Groningen — possible new availability detected:\n\n"
            + "\n\n".join(alerts)
            + "\n\n---\n"
            + "Studios disappear in ~20 min once someone clicks 'Let's book'.\n"
            + "If the page hasn't visibly changed, it may be a minor content edit — "
            + "still worth a quick look."
        )
        send_email(
            subject=f"[Xior Groningen] {len(alerts)} property change(s) detected!",
            body=body,
        )
    else:
        print("no changes detected this iteration")

    save_state(new_state)


def main() -> int:
    """Run check_once() in a loop for ~55 minutes, checking every 5 minutes.

    GitHub Actions throttles workflows scheduled every 5 minutes, so instead
    we schedule the workflow hourly and do the 5-minute polling inside this
    single long-running job.
    """
    CHECK_INTERVAL_S = 5 * 60          # 5 minutes between checks
    MAX_RUN_S = 55 * 60                # leave 5 min buffer before GitHub's 60-min timeout

    start = time.time()
    iteration = 0

    while True:
        iteration += 1
        elapsed = int(time.time() - start)
        print(f"\n=== iteration {iteration} (elapsed: {elapsed}s) ===")

        try:
            check_once()
        except Exception as e:
            import traceback
            print(f"iteration error: {e}")
            traceback.print_exc()
            # do NOT crash — try again next iteration

        # Decide whether to sleep or exit
        elapsed = time.time() - start
        if elapsed + CHECK_INTERVAL_S >= MAX_RUN_S:
            print(f"\ndone after {iteration} iterations ({int(elapsed)}s total)")
            return 0

        print(f"sleeping {CHECK_INTERVAL_S}s until next check...")
        time.sleep(CHECK_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
