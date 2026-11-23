#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hourly mailbox watch - the phone-free version.

Why this exists
---------------
Until now the mailbox was only read when the user said hello, and only by an
assistant with a Gmail connector. That connector is not available to a scheduled
GitHub Action, and it cannot be reached while nobody is at a keyboard. The user
asked for the opposite: read the mail every hour, decide without asking, and
write the result into the sheet.

What it does on every run
-------------------------
  1. Reads the mailbox over IMAP with an App Password (the same two secrets the
     radar already uses: EMAIL_USER, EMAIL_APP_PASSWORD). No new secrets.
  2. Classifies every message that is not obvious noise:
        Rejected  -> sheet Status = Rejected
        Interview -> sheet Status = Interview, Telegram alert
        Assessment-> sheet Status = Assessment, Telegram alert
        Applied   -> sheet Status = Applied
        Offer     -> sheet Status = Offer, Telegram alert
  3. Writes the status into Jobs!I:N and appends a line to the Inbox tab.
  4. Sends one Telegram digest per run that has any news, and one line for any
     security-relevant mail, whatever else happened.
  5. Records which message ids it has already dealt with, so a re-run is a no-op.

Design rules that came out of this week's mistakes
--------------------------------------------------
* A row that already reads Interview is never downgraded. A later automated
  acknowledgement must not erase a real invitation.
* A row that already reads Rejected stays Rejected unless a human-level event
  (Interview, Assessment, Offer) arrives afterwards, because ATS systems do send
  a rejection and then a different team writes back.
* The company is matched on a normalised key across the *whole* sheet, and the
  newest matching row wins. Matching only the newest row by name was how
  ImagineLive and Digitain collided.
* Nothing is ever reported as "no reply" from this script. Silence here means
  the script saw nothing, never that the employer said nothing.

Usage
-----
    python hourly_watch.py --dry-run     print what it would do, change nothing
    python hourly_watch.py --once        do it
    python hourly_watch.py --digest-only send a heartbeat without touching mail
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

IMAP_HOST = "imap.gmail.com"
STATE_FILE = Path(__file__).with_name("seen_hourly.json")
SHEET_RANGE = "Jobs!A:N"
INBOX_RANGE = "Inbox!A:F"

# Columns in Jobs, 1-based. Derived from the live header row, not guessed.
COL_COMPANY = 3
COL_TITLE = 4
COL_STATUS = 9
COL_LAST_REPLY = 13
COL_NEXT_ACTION = 14

NEXT_ACTION = {
    "Interview": "Reply and book a slot",
    "Assessment": "Do the test task, note the deadline",
    "Offer": "Read the offer. Reply within a day",
    "Rejected": "Closed. Move on",
    "Applied": "Wait. Follow up in 7 days",
}

# ---------------------------------------------------------------------------
# Senders that are never about one application. These are deleted on sight by
# the assistant, so they must never reach the sheet either.
NOISE_SENDERS = (
    "jobalerts-noreply@linkedin.com", "notifications@mail.talenthop.com",
    "updates@notify.talenthop.com", "alerts@updates.haystackapp.io",
    "notify@jobicy.com", "magic@djinni.co", "noreply@notifications.freelancer.com",
    "notifications.freelancer.com", "noreply@tradingview.com", "bitunix.com",
    "us-learning.alison.com", "alison.com", "coursera.org", "learn.coursera.org",
    "noreply@adzuna.de", "no-reply@adzuna.de", "aplikacje.pracuj.pl",
    "wysylka.pracuj.pl", "noreply@job.am", "noreply@email.fxpro.com",
    "email.fxpro.com", "getbring.com", "eplanetbrokers.com", "tabdeal.com",
    "coinglass.com", "toobit.com", "jitter.video", "altfins.com",
    "notifications@github.com", "noreply@github.com",
    "community@test.io", "no-reply@m.learn.coursera.org", "hello.tealhq.com",
)

# Mail that mentions money moving, a new device or a new credential. Separate
# from job mail, and always surfaced.
SECURITY = re.compile(
    r"(security info (?:was |has been )?added|new sign-?in|passkey|"
    r"password (?:was |has been )?(?:changed|reset)|recovery (?:email|phone)|"
    r"suspicious sign-?in|verify your (?:identity|account)|"
    r"two-step|2-step|unusual activity|api key (?:created|revoked))",
    re.I,
)

# Order matters. "Unfortunately we cannot invite you to an interview" contains an
# interview phrase, so rejection is tested first.
REJECTED = re.compile(
    r"(unfortunately|not (?:to )?(?:move|moving) forward|"
    r"will not be (?:moving|progressing|advancing)|"
    r"decided (?:not to proceed|to proceed with other)|"
    r"pursue other candidates|other candidates|another candidate|"
    r"position has been filled|no longer under consideration|"
    r"not been (?:selected|successful)|unsuccessful|regret to inform|"
    r"not the right fit|keep your (?:CV|resume|details) on file)",
    re.I,
)

OFFER = re.compile(
    r"(we are pleased to offer|offer of employment|job offer|"
    r"we would like to offer you|formal offer|offer letter)",
    re.I,
)

INTERVIEW = re.compile(
    r"(invite you to an interview|like to invite you|invitation to interview|"
    r"schedule (?:a|an|your) (?:call|interview|meeting|chat)|"
    r"book a (?:time|slot|call)|calendly|cal\.com|"
    r"availability for (?:a|an) (?:call|interview|meeting)|"
    r"(?:technical|final|first|introductory|screening) (?:interview|call|conversation)|"
    r"interview with (?:our|the|a) |"
    r"would like to (?:speak|talk|meet) with you|"
    r"next step (?:is|would be) (?:a|an|to)|"
    r"happy to move forward|proceed with your application)",
    re.I,
)

ASSESSMENT = re.compile(
    r"(test (?:task|assignment|exercise)|take[- ]home|home assignment|"
    r"coding (?:challenge|test|exercise)|assessment|"
    r"practical exercise|technical task|case study|quiz)",
    re.I,
)

APPLIED = re.compile(
    r"(your application (?:was|has been) (?:sent|submitted|received)|"
    r"(?:we|i) (?:have )?(?:received|got) your application|"
    r"application (?:successfully )?(?:received|submitted|confirmed)|"
    r"thank(?:s|you)? for (?:your application|applying|your interest)|"
    r"your application to |thanks for applying)",
    re.I,
)

NOT_ABOUT_ONE_APPLICATION = re.compile(
    r"(job alert|new jobs? (?:for|posted)|recommended jobs|"
    r"newsletter|unsubscribe from marketing|webinar|"
    r"your job search|complete your profile|weekly projects|"
    r"tester tournament|black friday)",
    re.I,
)

# ---------------------------------------------------------------------------
# Mail plumbing


def _first_address(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+", value or "")
    return match.group(0).lower() if match else ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    return _clean(text)


def _plain_body(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return _decode_part(part)
        for part in message.walk():
            if part.get_content_type() == "text/html":
                return _strip_html(_decode_part(part))
        return ""
    if message.get_content_type() == "text/html":
        return _strip_html(_decode_part(message))
    return _decode_part(message)


def imap_connect(box_name: str = "INBOX"):
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "EMAIL_USER and EMAIL_APP_PASSWORD are not set. Add both as "
            "repository secrets before this workflow can read the mailbox."
        )
    box = imaplib.IMAP4_SSL(IMAP_HOST)
    box.login(user, password)
    box.select(box_name)
    return box


def fetch_messages(days: int = 3, limit: int = 300, box_name: str = "INBOX"):
    """Yields one dict per message. Days is small on purpose: an hourly job that
    re-reads three days is still cheap, and a missed run repairs itself."""
    box = imap_connect(box_name)
    try:
        query = f'(X-GM-RAW "newer_than:{days}d")'
        try:
            status, data = box.search(None, query)
            if status != "OK":
                raise RuntimeError("X-GM-RAW refused")
        except Exception:
            since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            status, data = box.search(None, f"(SINCE {since})")
            if status != "OK":
                raise RuntimeError("IMAP search failed")
        ids = data[0].split()
        for msg_id in ids[-limit:]:
            status, payload = box.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            yield {
                "sender": _first_address(message.get("From", "")),
                "sender_raw": _decode_header(message.get("From", "")),
                "subject": _clean(_decode_header(message.get("Subject", ""))),
                "body": _plain_body(message),
                "date": message.get("Date", ""),
                "mid": message.get("Message-ID", "") or str(msg_id),
            }
    finally:
        try:
            box.close()
        except Exception:
            pass
        box.logout()


# ---------------------------------------------------------------------------
# Classification

COMPANY_STOPWORDS = {
    "your", "you", "we", "here", "thank", "thanks", "dear", "our", "this", "it",
    "please", "if", "at", "and", "the", "a", "an", "for", "to", "with", "is",
    "was", "has", "have", "on", "in", "from", "team", "role", "careers",
    "position", "application", "candidate", "us", "you're", "regards",
}

# A company name is a run of capitalised words. It has to stay case sensitive or
# it swallows whole sentences, which is how the old code produced "Broadridge
# Your". The anchors below are matched case-insensitively instead, because a
# confirmation almost always starts a sentence: "Thank you for applying at...".
CAP_RUN = re.compile(r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")

COMPANY_ANCHORS = [
    r"application (?:was |has been )?sent to\s+",
    r"application (?:to|for)[^.\n]{0,90}?\bat\s+",
    r"thank(?:s| you)[^.\n]{0,60}?(?:applying|application|interest|applied)"
    r"[^.\n]{0,90}?\b(?:at|to|in|with)\s+",
    r"(?:we|i)\s+(?:have\s+)?received your application[^.\n]{0,60}?\bat\s+",
    r"interest in\s+",
    r"your (?:application|interest|candidacy)[^.\n]{0,40}?(?:at|with)\s+",
    r"position at\s+",
    r"\bat\s+",
]


def _trim_company(value: str) -> str:
    words = _clean(value).strip(" .,").split()
    while words and words[-1].lower().strip(".,") in COMPANY_STOPWORDS:
        words.pop()
    return " ".join(words).strip(" .,")


def anchor_company(text: str) -> str:
    """Walks the anchor list and, at each hit, reads the capitalised run after it."""
    for anchor in COMPANY_ANCHORS:
        for match in re.finditer(anchor, text, re.I):
            tail = text[match.end():match.end() + 140]
            run = CAP_RUN.match(tail)
            if not run:
                continue
            name = _trim_company(run.group(1))
            if len(name) > 1 and name.lower() not in COMPANY_STOPWORDS:
                return name
    return ""


def known_company(text: str, known) -> str:
    """Falls back to asking whether any company already in the sheet is named in
    the mail. This is what makes 'Imagine Live' in the body resolve to the sheet's
    'ImagineLive' row, and what catches mails whose template puts the employer in
    a header the plain-text part drops."""
    folded = text.lower()
    best = ""
    for name in known:
        needle = (name or "").strip()
        if len(needle) < 3:
            continue
        # Compare on letters only so "Imagine Live" and "ImagineLive" are one name.
        loose = norm(needle)
        if len(loose) < 4:
            continue
        squeezed = re.sub(r"[^a-z0-9]", "", folded)
        if loose in squeezed and len(needle) > len(best):
            best = needle
    return best


# Applicant tracking systems put the employer in a subdomain or not at all, so a
# sender domain is a last-resort hint. "comm-it.comeet-notifications.com" is the
# only thing that identifies CommIT in their own confirmation mail.
ATS_DOMAINS = (
    "comeet-notifications.com", "bamboohr.com", "ashbyhq.com", "myworkday.com",
    "workablemail.com", "teamtailor.com", "greenhouse.io", "lever.co",
    "smartrecruiters.com", "recruitee.com", "jobvite.com", "icims.com",
    "breezy.hr", "zohorecruit.com", "hibob.com", "personio.de",
    "onlyfy.com", "softgarden.io", "join.com",
)


def from_domain_hint(sender: str) -> str:
    """'comm-it.comeet-notifications.com' -> 'comm-it'. Everything generic is
    thrown away so that 'noreply@bamboohr.com' yields nothing at all."""
    if "@" not in sender:
        return ""
    host = sender.split("@", 1)[1]
    for ats in ATS_DOMAINS:
        if host.endswith(ats):
            host = host[: -len(ats)].strip(".")
            break
    if not host:
        return ""
    label = host.split(".")[0]
    label = re.sub(r"^(?:no-?reply|notifications?|mail|email|e|m|jobs?|careers?|"
                   r"talent|hr|apply|applications?|app|candidates?|updates?|notify)$", "", label)
    # Hyphens go too: "comm-it" has to match the sheet's "CommIT".
    label = re.sub(r"[^a-z0-9]", "", label.lower())
    return label if len(label) > 2 else ""


def candidates_for(message, known=()) -> list:
    """Ordered guesses at the employer, best first.

    The anchor wins over a name that merely appears somewhere in the body, because
    a body can name a product: 'Thank you for applying at Digitain' with the
    position 'Senior QA Engineer | Manual (Imagine Live)' anchors Digitain but
    mentions Imagine Live. Both are worth trying, in that order, and the caller
    takes the first one that matches a row on the sheet.
    """
    text = f"{message['subject']} {message['body'][:800]}"
    ordered = []
    anchored = anchor_company(text)
    if anchored:
        canonical = ""
        for name in known:
            if norm(name) == norm(anchored):
                canonical = name
                break
        ordered.append(canonical or anchored)

    for name in known:
        loose = norm(name)
        if len(loose) < 4:
            continue
        if loose in re.sub(r"[^a-z0-9]", "", text.lower()):
            if name not in ordered:
                ordered.append(name)

    domain = from_domain_hint(message["sender"])
    if domain:
        for name in known:
            if norm(name) and (norm(name) in domain or domain in norm(name)):
                if name not in ordered:
                    ordered.insert(1, name)

    return ordered


def guess_company(message, known=()) -> str:
    ordered = candidates_for(message, known)
    return ordered[0] if ordered else ""


def is_noise(message) -> bool:
    sender = message["sender"]
    if any(noise in sender for noise in NOISE_SENDERS):
        return True
    text = f"{message['subject']} {message['body'][:600]}"
    return bool(NOT_ABOUT_ONE_APPLICATION.search(text))


def classify(message, known=()):
    """Returns (kind, company) or None. Rejection is tested before interview on
    purpose: 'unfortunately we cannot invite you' is a no."""
    text = f"{message['subject']}\n{message['body'][:3000]}"
    company = guess_company(message, known)
    if REJECTED.search(text):
        return "Rejected", company
    if OFFER.search(text):
        return "Offer", company
    if INTERVIEW.search(text):
        return "Interview", company
    if ASSESSMENT.search(text):
        return "Assessment", company
    if APPLIED.search(text):
        return "Applied", company
    return None


# ---------------------------------------------------------------------------
# Sheet plumbing - stdlib only, so the Action needs no Google client library.


def access_token() -> str:
    """Signed JWT -> access token, using the service account already in secrets."""
    import base64
    import time

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    info = json.loads(raw)
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claim = {
        "iss": info["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now,
    }

    def b64(obj):
        blob = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(blob).rstrip(b"=")

    signing_input = b".".join([b64(header), b64(claim)])
    key = serialization.load_pem_private_key(
        info["private_key"].encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwt = signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt.decode(),
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())["access_token"]


def sheets_get(token, spreadsheet_id, cell_range):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
           f"/values/{urllib.parse.quote(cell_range)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read()).get("values", [])


def sheets_update(token, spreadsheet_id, cell_range, values):
    body = json.dumps({"values": values}).encode()
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
           f"/values/{urllib.parse.quote(cell_range)}?valueInputOption=USER_ENTERED")
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())


def sheets_append(token, spreadsheet_id, cell_range, rows):
    body = json.dumps({"values": rows}).encode()
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
           f"/values/{urllib.parse.quote(cell_range)}:append"
           f"?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())


def norm(text: str) -> str:
    """Company keys collapse to letters and digits only. 'Digitain / Imagine Live',
    'ImagineLive' and 'Imagine Live' all become different keys on purpose - the
    sheet row is chosen by the title as well, see pick_row."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Words that say nothing about WHICH application at a company a mail refers to.
# What is left - "imagine", "live", "presenter", "persian", "automation" - is what
# separates Digitain's three simultaneous rows from one another.
TITLE_STOPWORDS = {
    "senior", "middle", "junior", "lead", "principal", "staff", "chief", "head",
    "the", "and", "for", "with", "all", "genders", "role", "position", "vacancy",
    "engineer", "engineering", "developer", "analyst", "specialist", "consultant",
    "test", "tester", "testing", "qa", "qc", "quality", "assurance", "control",
    "remote", "hybrid", "onsite", "full", "time", "part", "contract", "permanent",
    "ii", "iii", "iv", "one", "two", "new", "job", "jobs",
}


def title_tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
            if w not in TITLE_STOPWORDS}


def pick_row(rows, company, kind="", hint=""):
    """Chooses the row to update, out of every row that matches the company.

    Company alone is not enough. Digitain had three applications open at once -
    Senior QA Engineer, Persian Speaking Game Presenter and Georgian Speaking Game
    Presenter - and writing a QA rejection onto the presenter row is a silent,
    plausible-looking error. So the job title is matched too.

    hint is the text the mail itself used to identify the position, usually the
    subject plus the first lines of the body, because ATS confirmations name the
    role in the body and not in the subject.

    When the mail gives nothing to match a title on, the newest matching row wins,
    which is the same guess the old code made - but now it is the fallback rather
    than the only behaviour.
    """
    target = norm(company)
    if not target:
        return None
    candidates = []
    for index, row in enumerate(rows[1:], start=2):
        if len(row) < COL_COMPANY:
            continue
        cell = norm(row[COL_COMPANY - 1])
        if not cell:
            continue
        if cell == target or (len(target) > 4 and (target in cell or cell in target)):
            candidates.append(index)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    hint_tokens = title_tokens(hint)
    if not hint_tokens:
        return candidates[-1]

    best_row, best_score = candidates[-1], 0
    for index in candidates:
        row = rows[index - 1]
        title = row[COL_TITLE - 1] if len(row) >= COL_TITLE else ""
        score = len(title_tokens(title) & hint_tokens)
        if score >= best_score:
            best_row, best_score = index, score
    return best_row


def current_status(rows, row_number):
    row = rows[row_number - 1]
    if len(row) < COL_STATUS:
        return ""
    return (row[COL_STATUS - 1] or "").strip()


def apply_status(token, spreadsheet_id, rows, company, kind, when, hint=""):
    """Writes one row and returns (row_number, old, new, matched_name) or None.

    company may be a list of candidates ordered best-first; the first one that
    matches a row on the sheet wins. That is what stops the Digitain mail being
    written onto the ImagineLive row.
    """
    names = company if isinstance(company, (list, tuple)) else [company]
    row_number, matched = None, ""
    for name in names:
        row_number = pick_row(rows, name, kind, hint)
        if row_number is not None:
            matched = name
            break
    if row_number is None:
        return None
    company = matched
    old = current_status(rows, row_number)

    # Never walk a real event backwards.
    order = {"": 0, "Applied": 1, "Assessment": 2, "Interview": 3,
             "Offer": 4, "Rejected": 2}
    if kind == "Applied" and old in ("Interview", "Assessment", "Offer", "Rejected"):
        return None
    if kind == "Rejected" and old in ("Interview", "Offer"):
        return None
    if order.get(kind, 0) < order.get(old, 0) and kind != "Rejected":
        return None

    stamp = when or str(date.today())
    sheets_update(token, spreadsheet_id, f"Jobs!I{row_number}:N{row_number}",
                  [[kind, "", "", stamp, NEXT_ACTION.get(kind, "")]])
    rows[row_number - 1] = (
        list(rows[row_number - 1]) + [""] * 14)[:14]
    rows[row_number - 1][COL_STATUS - 1] = kind
    return row_number, old, kind, matched


# ---------------------------------------------------------------------------
# Telegram


def telegram_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured, skipping the push.")
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=body, timeout=30) as response:
            return json.loads(response.read()).get("ok", False)
    except Exception as exc:
        print(f"Telegram send failed: {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# State


def load_seen() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else data.keys())
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    STATE_FILE.write_text(
        json.dumps(sorted(seen)[-8000:], ensure_ascii=False, indent=1),
        encoding="utf-8")


# ---------------------------------------------------------------------------
# The run


def run(dry_run: bool = False, days: int = 3) -> list:
    seen = load_seen()
    events, security = [], []

    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    token, rows, known = None, [], []
    if spreadsheet_id:
        try:
            token = access_token()
            rows = sheets_get(token, spreadsheet_id, SHEET_RANGE)
            known = [row[COL_COMPANY - 1] for row in rows[1:]
                     if len(row) >= COL_COMPANY and row[COL_COMPANY - 1]]
        except Exception as exc:
            print(f"Could not read the sheet first: {type(exc).__name__}: {exc}")

    for message in fetch_messages(days=days):
        mid = message["mid"]
        if mid in seen:
            continue

        if SECURITY.search(f"{message['subject']}\n{message['body'][:3000]}"):
            security.append(message)
            seen.add(mid)
            continue

        if is_noise(message):
            seen.add(mid)
            continue

        verdict = classify(message, known)
        seen.add(mid)
        if not verdict:
            continue
        kind, company = verdict
        events.append({"kind": kind, "company": company,
                       "candidates": candidates_for(message, known),
                       "hint": f"{message['subject']} {message['body'][:400]}",
                       "subject": message["subject"],
                       "sender": message["sender"],
                       "date": message["date"]})

    if not events and not security:
        print("Nothing new.")
        if not dry_run:
            save_seen(seen)
        return []

    print(f"{len(events)} event(s), {len(security)} security item(s)")
    for item in events:
        print(f"  [{item['kind']:10s}] {item['company'] or '(unknown)':28s} "
              f"{item['subject'][:60]}")

    if dry_run:
        for message in security:
            print(f"  [SECURITY  ] {message['subject'][:70]}")
        print("\nDry run: nothing written, nothing sent.")
        return events

    changes, orphans = [], []
    if spreadsheet_id and token:
        try:
            for item in events:
                if not item["company"]:
                    orphans.append(item)
                    continue
                result = apply_status(token, spreadsheet_id, rows,
                                      item.get("candidates") or item["company"],
                                      item["kind"], item["date"],
                                      item.get("hint", ""))
                if result:
                    changes.append(result)
                    print(f"  sheet: Jobs row {result[0]} {result[3]} "
                          f"{result[1] or '(blank)'} -> {result[2]}")
                else:
                    orphans.append(item)
                    print(f"  sheet: no row for {item['company']} ({item['kind']})")
            if events:
                sheets_append(token, spreadsheet_id, INBOX_RANGE, [
                    [str(date.today()), item["kind"], item["company"],
                     item["subject"], item["sender"], item["date"]]
                    for item in events])
        except Exception as exc:
            print(f"Sheet update failed: {type(exc).__name__}: {exc}")

    # One Telegram message per run. An interview is time critical, a rejection is
    # not, and eight separate pushes for eight rejections trains the user to
    # ignore the channel.
    lines = []
    urgent = [e for e in events if e["kind"] in ("Interview", "Assessment", "Offer")]
    if urgent:
        lines.append("URGENT")
        for item in urgent:
            lines.append(f"{item['kind'].upper()}: {item['company'] or '?'}")
            lines.append(f"  {item['subject'][:90]}")
        lines.append("")
    rest = [e for e in events if e not in urgent]
    if rest:
        lines.append("Also new:")
        for item in rest:
            lines.append(f"  {item['kind']:10s} {item['company'] or '?'}")
    if orphans:
        lines.append("")
        lines.append(f"{len(orphans)} item(s) had no matching sheet row.")
    for message in security:
        lines.append("")
        lines.append(f"SECURITY: {message['subject'][:90]}")
        lines.append("  Check whether you did this.")
    if lines:
        telegram_send("\n".join(lines).strip())

    save_seen(seen)
    return events


def digest(dry_run: bool = False) -> None:
    """A heartbeat, so silence from the workflow is never ambiguous."""
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    waiting, actionable = [], []
    if spreadsheet_id:
        try:
            token = access_token()
            rows = sheets_get(token, spreadsheet_id, SHEET_RANGE)
            for index, row in enumerate(rows[1:], start=2):
                status = (row[COL_STATUS - 1] if len(row) >= COL_STATUS else "") or ""
                if status in ("Interview", "Assessment", "Offer"):
                    actionable.append((index, row[COL_COMPANY - 1], row[COL_TITLE - 1],
                                       status))
                elif status == "Applied":
                    waiting.append((index, row[COL_COMPANY - 1]))
        except Exception as exc:
            print(f"Digest read failed: {type(exc).__name__}: {exc}")
    lines = ["Hourly check is alive."]
    if actionable:
        lines.append("")
        lines.append("Needs you:")
        for index, company, title, status in actionable:
            lines.append(f"  row {index}  {status}: {company} - {title}")
    lines.append("")
    lines.append(f"{len(waiting)} application(s) waiting. Nothing else needed.")
    text = "\n".join(lines)
    print(text)
    if not dry_run:
        telegram_send(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--digest-only", action="store_true")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    try:
        if args.digest_only:
            digest(dry_run=args.dry_run)
        else:
            run(dry_run=args.dry_run, days=args.days)
    except Exception as exc:
        print(f"Hourly watch failed: {type(exc).__name__}: {exc}")
        if not args.dry_run:
            telegram_send(f"Hourly watch FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
