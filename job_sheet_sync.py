#!/usr/bin/env python3
"""
Push QA Job Radar results into the Google Sheet tracker.

This is the piece that turns the tracker from something I fill in by hand into
something that updates itself on the GitHub Actions schedule.

Two ways to authenticate:

  SERVICE ACCOUNT (recommended for the cron job)
    1. Google Cloud console, new project, enable the Google Sheets API
    2. Create a service account, create a JSON key
    3. Share the spreadsheet with the service account email as Editor
    4. Put the whole JSON in a GitHub secret called GOOGLE_SERVICE_ACCOUNT_JSON

  OAUTH REFRESH TOKEN (if you already have one)
    Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN

Environment:
  SPREADSHEET_ID            the id from the sheet URL
  GOOGLE_SERVICE_ACCOUNT_JSON  OR the three OAuth variables above

Usage:
    python job_sheet_sync.py --dry-run    show what would be written
    python job_sheet_sync.py              write new rows only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date

SHEET_RANGE = "Jobs!A:L"
COLUMNS = 12


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def access_token() -> str:
    """Returns a bearer token, from a service account or an OAuth refresh token."""
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if sa_json:
        # Service account: sign a JWT, exchange it for a token.
        # Done by hand so the script keeps zero third-party dependencies, which
        # matters inside a GitHub Action where every install is a failure point.
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        import base64
        import time

        info = json.loads(sa_json)
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claim = {
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        def b64(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        signing_input = f"{b64(header)}.{b64(claim)}".encode()
        key = serialization.load_pem_private_key(
            info["private_key"].encode(), password=None
        )
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        jwt = signing_input.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

        payload = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=payload, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["access_token"]

    # OAuth refresh token path
    payload = urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=payload, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


# --------------------------------------------------------------------------- #
# Sheets calls
# --------------------------------------------------------------------------- #

def sheets_get(token: str, spreadsheet_id: str, rng: str):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
           f"/values/{urllib.parse.quote(rng)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("values", [])


def sheets_append(token: str, spreadsheet_id: str, rows: list):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
           f"/values/{urllib.parse.quote(SHEET_RANGE)}:append"
           "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    body = json.dumps({"values": rows}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #

TIER_LABEL = {
    1: "1 Armenia",
    2: "2 Remote open",
    3: "3 Remote 1 country",
    4: "4 Sponsorship",
}


def existing_keys(rows) -> set:
    """Company + title pairs already in the sheet, so nothing is written twice."""
    keys = set()
    for row in rows[1:]:  # skip header
        if len(row) < 4:
            continue
        company = re.sub(r"[^a-z0-9]", "", (row[2] or "").lower())
        title = re.sub(r"[^a-z0-9]", "", (row[3] or "").lower())
        keys.add(f"{company}|{title}")
    return keys


def row_key(job) -> str:
    company = re.sub(r"[^a-z0-9]", "", job["company"].lower())
    title = re.sub(r"[^a-z0-9]", "", job["title"].lower())
    return f"{company}|{title}"


def to_row(job) -> list:
    tier = TIER_LABEL.get(job.get("tier"), "")
    return [
        str(date.today()),
        tier,
        job["company"],
        job["title"],
        job["location"],
        str(job.get("score", "")),
        job["source"],
        "",              # salary, not in the radar output
        "Not applied",   # status, for you to change
        "",              # applied date
        job.get("url", ""),
        "",              # notes
    ] + [""] * max(0, COLUMNS - 12)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def sync_file(path: str, dry_run: bool = False):
    """Appends the jobs in a JSON file to the sheet. Returns (added, skipped).

    Called from job_radar.py after the Telegram sends, so the sheet always holds
    exactly what was sent rather than the results of a second scan.
    """
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("SPREADSHEET_ID is not set")

    with open(path, encoding="utf-8") as fh:
        jobs = json.load(fh)

    token = access_token()
    current = sheets_get(token, spreadsheet_id, SHEET_RANGE)
    known = existing_keys(current)

    fresh = [j for j in jobs if row_key(j) not in known]
    skipped = len(jobs) - len(fresh)
    if not fresh:
        return 0, skipped

    rows = [to_row(j) for j in fresh]
    if dry_run:
        return len(rows), skipped

    sheets_append(token, spreadsheet_id, rows)
    return len(rows), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--input", default=None,
                    help="JSON file of jobs. Defaults to sent_jobs.json if present, "
                         "otherwise radar_jobs.json")
    args = ap.parse_args()

    if not os.environ.get("SPREADSHEET_ID"):
        sys.exit("SPREADSHEET_ID is not set")

    source = args.input
    if source is None:
        source = "sent_jobs.json" if os.path.exists("sent_jobs.json") else "radar_jobs.json"

    try:
        added, skipped = sync_file(source, dry_run=args.dry_run)
    except FileNotFoundError:
        sys.exit(
            f"{source} not found. Run the radar first:\n"
            "  python job_radar.py --once      (writes sent_jobs.json)\n"
            "  python job_radar.py --json      (writes radar_jobs.json)"
        )

    print(f"{source}: {added} rows {'would be ' if args.dry_run else ''}appended, "
          f"{skipped} already in the sheet")

    token = access_token()
    current = sheets_get(token, spreadsheet_id, SHEET_RANGE)
    known = existing_keys(current)

    fresh = [j for j in jobs if row_key(j) not in known]
    print(f"{len(jobs)} radar jobs, {len(known)} already in the sheet, {len(fresh)} new")

    if not fresh:
        return

    rows = [to_row(j) for j in fresh]

    if args.dry_run:
        for r in rows[:15]:
            print("  ", r[1], "|", r[2], "|", r[3], "|", r[4])
        print(f"\n{len(rows)} rows would be appended")
        return

    result = sheets_append(token, spreadsheet_id, rows)
    print("Appended:", result.get("updates", {}).get("updatedRange"))


if __name__ == "__main__":
    main()
