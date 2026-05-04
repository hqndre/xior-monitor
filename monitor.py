#!/usr/bin/env python3
"""
Xior Groningen studio availability monitor.

Loads the Xior booking site in a headless browser, scans for studios
in Groningen, and emails when new ones appear. Designed to run every
15 minutes on GitHub Actions.

State is kept in state.json (committed back to the repo by the workflow)
so we only email on *changes*, not every run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ----- config -----
BOOKING_URL = "https://www.xior-booking.com/"
CITY = "Groningen"
STATE_FILE = Path(__file__).parent / "state.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def render_booking_page() -> str:
    """Open the booking site in headless Chromium, return the rendered HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        page.goto(BOOKING_URL, wait_until="networkidle", timeout=60_000)
        # let any late-loading JS / lazy lists settle
        page.wait_for_timeout(5_000)

        # Try to scroll to force-load any virtualized lists
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)
        except Exception:
            pass

        html = page.content()
        browser.close()

    # Detect Cloudflare-style challenges so we fail loudly instead of silently
    if "Just a moment" in html or "challenge-platform" in html:
        raise RuntimeError(
            "Booking site returned a bot-protection challenge. "
            "The script needs adjustment (e.g. add stealth plugin)."
        )
    return html


def extract_groningen_studios(html: str) -> list[dict]:
    """
    Find studio listings for Groningen in the rendered HTML.
    Returns a list of {sig, text, url} dicts.

    Strategy: find anchor tags whose visible text or href references
    Groningen. These are stable across page reloads. Fall back to
    hashed card text for any cards without a clear link.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}  # sig -> details

    # 1) anchors mentioning Groningen
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = " ".join(a.get_text(" ", strip=True).split())
        haystack = (href + " " + text).lower()
        if CITY.lower() in haystack:
            sig = f"href:{href}"
            # try to grab a richer description from the surrounding card
            card = a.find_parent(["article", "li", "div"])
            desc = " ".join(card.get_text(" ", strip=True).split())[:300] if card else text
            found[sig] = {
                "sig": sig,
                "text": desc or text or href,
                "url": _normalize_url(href),
            }

    # 2) cards mentioning Groningen + a price (€) or size (m²)
    #    only used if strategy 1 missed them
    for el in soup.find_all(["article", "li", "div"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if (
            CITY.lower() in text.lower()
            and ("€" in text or "m²" in text or " m2" in text.lower())
            and len(text) < 600  # don't match the whole page wrapper
        ):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
            sig = f"card:{digest}"
            if sig not in found:
                found[sig] = {"sig": sig, "text": text[:300], "url": BOOKING_URL}

    return sorted(found.values(), key=lambda d: d["sig"])


def _normalize_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.xior-booking.com" + href
    return BOOKING_URL


def load_state() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()).get("studios", []))
        except Exception as e:
            print(f"warn: could not parse state.json ({e}); starting fresh")
    return set()


def save_state(studios: list[dict]) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {"studios": [s["sig"] for s in studios]},
            indent=2,
            sort_keys=True,
        )
    )


def send_email(subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_email = os.environ.get("TO_EMAIL", smtp_user)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [to_email], msg.as_string())
    print(f"email sent to {to_email}: {subject}")


def main() -> int:
    print("fetching xior-booking.com ...")
    html = render_booking_page()
    studios = extract_groningen_studios(html)
    print(f"found {len(studios)} Groningen listing(s)")

    previous = load_state()
    new_studios = [s for s in studios if s["sig"] not in previous]

    if new_studios:
        lines = [
            f"{len(new_studios)} new Groningen studio listing(s) on Xior:",
            "",
        ]
        for s in new_studios:
            lines.append(f"• {s['text']}")
            lines.append(f"  → {s['url']}")
            lines.append("")
        lines.append(f"Book fast — studios disappear in ~20 min once someone clicks.")
        lines.append(f"Booking site: {BOOKING_URL}")

        send_email(
            subject=f"[Xior] {len(new_studios)} new Groningen studio(s) available",
            body="\n".join(lines),
        )
    else:
        print("no new studios — nothing to do")

    save_state(studios)
    return 0


if __name__ == "__main__":
    sys.exit(main())
