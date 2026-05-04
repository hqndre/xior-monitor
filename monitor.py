#!/usr/bin/env python3
"""
Xior Groningen studio availability monitor.

Loads the Xior booking site in a headless browser, scans for studios
in Groningen, and emails when new ones appear. Designed to run every
5 minutes on GitHub Actions.

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
import time
from email.mime.text import MIMEText
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
    HAVE_STEALTH = True
except ImportError:
    HAVE_STEALTH = False

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
    """Open the booking site in headless Chromium, return rendered HTML.

    Uses playwright-stealth to evade basic Cloudflare bot detection,
    and waits patiently for any 'Just a moment...' challenge to resolve.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="Europe/Amsterdam",
        )
        page = ctx.new_page()
        if HAVE_STEALTH:
            stealth_sync(page)
        else:
            print("warn: playwright-stealth not installed; running without it")

        page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60_000)

        # Wait up to 45s for Cloudflare's "Just a moment..." challenge to clear.
        deadline = time.time() + 45
        cleared = False
        while time.time() < deadline:
            content = page.content()
            if "Just a moment" not in content and "challenge-platform" not in content:
                cleared = True
                break
            page.wait_for_timeout(1500)

        if not cleared:
            raise RuntimeError(
                "Bot-protection challenge did not clear within 45 seconds. "
                "Xior may have tightened protection."
            )

        # Let any post-challenge JS render the listings
        page.wait_for_timeout(5_000)

        # Force-load lazy lists by scrolling
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)
        except Exception:
            pass

        html = page.content()
        browser.close()

    return html


def extract_groningen_studios(html: str) -> list[dict]:
    """
    Find studio listings for Groningen in the rendered HTML.
    Returns a list of {sig, text, url} dicts.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}

    # 1) anchors mentioning Groningen
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = " ".join(a.get_text(" ", strip=True).split())
        haystack = (href + " " + text).lower()
        if CITY.lower() in haystack:
            sig = f"href:{href}"
            card = a.find_parent(["article", "li", "div"])
            desc = " ".join(card.get_text(" ", strip=True).split())[:300] if card else text
            found[sig] = {
                "sig": sig,
                "text": desc or text or href,
                "url": _normalize_url(href),
            }

    # 2) cards mentioning Groningen + a price (€) or size (m²)
    for el in soup.find_all(["article", "li", "div"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if (
            CITY.lower() in text.lower()
            and ("€" in text or "m²" in text or " m2" in text.lower())
            and len(text) < 600
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
            lines.append(f"  -> {s['url']}")
            lines.append("")
        lines.append("Book fast — studios disappear in ~20 min once someone clicks.")
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
