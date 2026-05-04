#!/usr/bin/env python3
"""
Xior Groningen studio availability monitor (Scrape.do edition).

Fetches the booking site through Scrape.do's residential proxy +
JS-rendering API, which bypasses Cloudflare. Designed to run hourly
on GitHub Actions to stay within the 1000 free calls/month budget.

State is kept in state.json (committed back by the workflow) so we
only email on *changes*, not every run.
"""

from __future__ import annotations

import hashlib
import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ----- config -----
BOOKING_URL = "https://www.xior-booking.com/"
CITY = "Groningen"
STATE_FILE = Path(__file__).parent / "state.json"
SCRAPE_DO_API = "https://api.scrape.do/"


def render_booking_page() -> str:
    """Fetch the booking site via Scrape.do (Cloudflare bypass + JS render)."""
    token = os.environ["SCRAPE_DO_TOKEN"]

    params = {
        "token": token,
        "url": BOOKING_URL,
        "render": "true",       # JS rendering — Scrape.do handles anti-bot automatically with this alone
    }

    print(f"requesting {BOOKING_URL} via Scrape.do ...")
    try:
        resp = requests.get(SCRAPE_DO_API, params=params, timeout=120)
    except requests.RequestException as e:
        raise RuntimeError(f"Scrape.do request failed: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"Scrape.do returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    html = resp.text
    print(f"got {len(html):,} bytes of HTML")

    # Sanity check — only flag as a challenge page if the <title> is clearly
    # a Cloudflare challenge. Note: Cloudflare injects a script reference to
    # /cdn-cgi/challenge-platform/ on EVERY page it protects (including
    # successful ones), so we cannot use that as a signal.
    soup_head = BeautifulSoup(html, "html.parser")
    title = (soup_head.title.string or "").strip() if soup_head.title else ""
    print(f"page title: {title!r}")

    if "Just a moment" in title or "Attention Required" in title:
        raise RuntimeError(
            f"Cloudflare challenge page detected (title: {title!r}). "
            "Scrape.do failed to clear it."
        )

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
