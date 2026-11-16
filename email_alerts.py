#!/usr/bin/env python3
"""Read job-alert email and turn it into the same job dicts the radar uses.

Why this exists. On a normal day the inbox produced 201 job-related messages in
14 days, and the single biggest sender was LinkedIn Job Alerts, which sent the
same posting four times before lunch. As a feed that is unusable. As a source it
is the most valuable one Ahmad has, because it is the only place that knows
which alerts LinkedIn actually matched to his profile, and the only place that
confirms an application went out.

Two things this does, in this order of importance:

  1. Applications and rejections. "Your application was sent to X" and the
     acknowledgement mails from an ATS tell the tracker what really happened.
     Without them the sheet said "Not applied" for Broadridge and Eleving on
     days when both had already been applied to.
  2. New postings, deduplicated by LinkedIn job id, so four copies of one alert
     become one row.

Access. Gmail over IMAP with an App Password, because that is the only method
that works unattended inside a GitHub Action without an OAuth dance. It needs
2-step verification on the Google account first:

    Google Account, Security, 2-Step Verification, then App passwords
    Create one called "qa-job-radar" and copy the 16 character value

Then set two repository secrets:

    EMAIL_USER            zarei.ahmad1992@gmail.com
    EMAIL_APP_PASSWORD    the 16 character app password, no spaces

Nothing here reads the mailbox for anything except these alerts. Messages are
opened, parsed in memory and never stored, and only the job fields survive.

Usage:
    python email_alerts.py                 parse recent alerts, print them
    python email_alerts.py --applications  show the application confirmations
    python email_alerts.py --json          write email_jobs.json for the radar
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

IMAP_HOST = "imap.gmail.com"

# Senders whose mail is worth opening. Everything else in the inbox is skipped
# before it is even downloaded, which keeps a run to a few seconds.
ALERT_SENDERS = [
    "jobalerts-noreply@linkedin.com",     # job alert digests, several a day
    "jobs-noreply@linkedin.com",         # application confirmations
    "notifications@djinni.co",
    "staff.am",
    "arbeitnow",
    "jobicy",
    "himalayas.app",
    "remotive",
    "weworkremotely",
    "landing.jobs",
    "no-reply@wysylka.pracuj.pl",
    "rekomendacje@wysylka.pracuj.pl",
]

LINKEDIN_JOB_URL = re.compile(r"/jobs/view/(?:[^/\s]*?-)?(\d{6,})")
DJINNI_JOB_URL = re.compile(r"djinni\.co/jobs/(\d+)-([a-z0-9-]+)")
GENERIC_APPLIED = re.compile(
    r"(your application (?:was|has been) (?:sent|submitted)|"
    r"your application to |"
    r"thank you for (?:your application|applying|your interest)|"
    r"we (?:have )?received your application|"
    r"application (?:received|confirmed|submitted)|"
    r"thanks for applying)",
    re.I,
)
REJECTION = re.compile(
    r"(unfortunately|not to move forward|will not be (?:moving|progressing)|"
    r"decided not to proceed|other candidates|position has been filled|"
    r"no longer under consideration)",
    re.I,
)

# The LinkedIn digest block, which is four lines and then the link:
#     Quality Engineer
#     TaskVerse
#     Germany
#     View job: https://...
DIGEST_BLOCK = re.compile(
    r"(?P<title>[^\r\n]{3,90})\r?\n"
    r"(?P<company>[^\r\n]{1,80})\r?\n"
    r"(?P<location>[^\r\n]{1,80})\r?\n"
    r"\s*View job:\s*(?P<url>https?://\S+)"
)

SUBJECT_NOISE = re.compile(
    r"^(?:re:\s*)?(?:job alert:?\s*)?|"
    r"\s*(?:and \d+ other jobs?.*|is hiring:?|is hiring for .*|"
    r"job at .*|you may be a fit for .*|and \d+ other.*)$",
    re.I,
)


def _first_address(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+", value or "")
    return match.group(0).lower() if match else ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def imap_connect():
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "EMAIL_USER and EMAIL_APP_PASSWORD are not set. "
            "Create a Gmail App Password and add both as repository secrets."
        )
    box = imaplib.IMAP4_SSL(IMAP_HOST)
    box.login(user, password)
    box.select("INBOX")
    return box


def fetch_alert_messages(days: int = 14, limit: int = 400):
    """Yields (sender, subject, plain body, date) for alert mail only."""
    box = imap_connect()
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        # Gmail supports its own search syntax over IMAP, which is far more
        # precise than IMAP's built in date search. Fall back if it is refused.
        ids = []
        try:
            query = " ".join(f"from:{s}" for s in ALERT_SENDERS[:1])
            query = f'(X-GM-RAW "newer_than:{days}d {{from:linkedin.com from:djinni.co from:staff.am from:arbeitnow from:jobicy.org from:himalayas.app from:remotive.com from:weworkremotely.com from:landing.jobs from:pracuj.pl}}")'
            status, data = box.search(None, query)
            if status == "OK":
                ids = data[0].split()
        except Exception:
            ids = []
        if not ids:
            status, data = box.search(None, f'(SINCE {since})')
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            ids = data[0].split()

        for msg_id in ids[-limit:]:
            status, payload = box.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            sender = _first_address(message.get("From", ""))
            if not any(s in sender for s in ALERT_SENDERS) and "linkedin" not in sender:
                continue
            body = _plain_body(message)
            yield {
                "sender": sender,
                "subject": _clean(_decode_header(message.get("Subject", ""))),
                "body": body,
                "date": message.get("Date", ""),
                "mid": message.get("Message-ID", ""),
            }
    finally:
        try:
            box.close()
        except Exception:
            pass
        box.logout()


def _decode_header(value: str) -> str:
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _plain_body(message) -> str:
    """Prefers text/plain. Falls back to stripped HTML, which some senders use
    for everything."""
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


def _decode_part(part) -> str:
    try:
        raw = part.get_payload(decode=True)
        if raw is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"[ \t]+", " ", text)


def parse_jobs(message) -> list:
    """Structured digest blocks first, then any stray job link."""
    found = []
    seen = set()
    sender = message["sender"]
    for block in DIGEST_BLOCK.finditer(message["body"]):
        url = block.group("url")
        job_id = _linkedin_id(url) or url
        if job_id in seen:
            continue
        title = _clean(block.group("title"))
        company = _clean(block.group("company"))
        location = _clean(block.group("location"))
        if not title or title.lower().startswith("view job"):
            continue
        seen.add(job_id)
        found.append(_job(title, company, location, url, sender, message["date"]))

    # Fallback: emails whose layout is not the digest block, but which still
    # carry a job URL. The subject usually names the role.
    for match in re.finditer(r"https?://[^\s\"'<>]*?/jobs/view/[^\s\"'<>]+", message["body"]):
        url = match.group(0)
        job_id = _linkedin_id(url) or url
        if job_id in seen:
            continue
        seen.add(job_id)
        title = SUBJECT_NOISE.sub("", message["subject"]).strip() or "QA role"
        found.append(_job(title, "", "", url, sender, message["date"]))

    for match in DJINNI_JOB_URL.finditer(message["body"]):
        slug = match.group(2)
        key = "djinni:" + match.group(1)
        if key in seen:
            continue
        seen.add(key)
        title = slug.replace("-", " ").title()
        found.append(_job(title, "", "", f"https://djinni.co/jobs/{match.group(1)}-{slug}/",
                          sender, message["date"]))
    return found


def _linkedin_id(url: str) -> str:
    match = LINKEDIN_JOB_URL.search(url)
    return "li:" + match.group(1) if match else ""


def _job(title, company, location, url, sender, date) -> dict:
    source = "LinkedIn email" if "linkedin" in sender else f"Email ({sender.split('@')[-1]})"
    # Strip tracking parameters: the same posting must dedupe against the copy
    # the radar found by scraping.
    clean_url = re.sub(r"\?.*$", "", url)
    return {
        "title": title,
        "company": company,
        "location": location,
        "description": "",
        "source": source,
        "url": clean_url,
        "remote": "remote" in (location or "").lower() or "remote" in (title or "").lower(),
        "relocation": False,
        "salary": "",
        "created": date,
        "tier": None,
        "score": 0,
        "restrictions": [location] if location else [],
    }


def parse_applications(message) -> list:
    """Application confirmations and rejections, which the tracker needs more
    than it needs another alert."""
    body = message["body"] + " " + message["subject"]
    if not (GENERIC_APPLIED.search(body) or REJECTION.search(body)):
        return []
    if "job alert" in message["subject"].lower():
        return []
    company = _guess_company(message)
    status = "Rejected" if REJECTION.search(body) and not GENERIC_APPLIED.search(body) else "Applied"
    return [{
        "company": company,
        "subject": message["subject"],
        "status": status,
        "date": message["date"],
        "sender": message["sender"],
    }]


def _guess_company(message) -> str:
    """Pulls the employer out of 'your application was sent to X', or out of the
    subject when the phrasing is the ATS style."""
    text = f"{message['subject']} {message['body'][:400]}"
    # A company name is a run of capitalised words and stops at the first
    # lowercase one. Without that, "sent to Broadridge Your application" was
    # being captured whole.
    name = r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
    patterns = [
        rf"application (?:was |has been )?sent to {name}",
        rf"application to .+? at {name}",
        rf"thank you for your interest in .*?\bat {name}",
        rf"interest in .{{0,20}}(?:career|candidate).{{0,10}}(?:at|with) {name}",
        rf"(?:application|candidacy|position).{{0,50}}?\bat {name}",
        rf"\bat {name} (?:role|team|careers)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _trim_company(match.group(1))
    return ""


# Words that start the next sentence rather than the company name. Without this
# "sent to Broadridge Your application" captured "Broadridge Your".
COMPANY_STOPWORDS = {
    "your", "you", "we", "here", "thank", "thanks", "dear", "our", "this", "it",
    "please", "if", "at", "and", "the", "a", "an", "for", "to", "with", "is",
    "was", "has", "have", "on", "in", "from", "team", "role", "careers",
}


def _trim_company(value: str) -> str:
    words = _clean(value).strip(" .,").split()
    while words and words[-1].lower().strip(".,") in COMPANY_STOPWORDS:
        words.pop()
    return " ".join(words).strip(" .,")


def collect(days: int = 14):
    jobs, applications, sources = [], [], {}
    for message in fetch_alert_messages(days=days):
        sources[message["sender"]] = sources.get(message["sender"], 0) + 1
        jobs.extend(parse_jobs(message))
        applications.extend(parse_applications(message))

    # One row per posting, no matter how many times it was mailed.
    by_id = {}
    for job in jobs:
        key = job["url"] or (job["title"] + "|" + job["company"])
        existing = by_id.get(key)
        if existing is None or len(job["company"]) > len(existing["company"]):
            by_id[key] = job
    return list(by_id.values()), applications, sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--applications", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    try:
        jobs, applications, sources = collect(days=args.days)
    except Exception as exc:
        print(f"Email read failed: {type(exc).__name__}: {exc}")
        sys.exit(1)

    print(f"Senders seen: {sources}")
    print(f"Unique postings after dedupe: {len(jobs)}")

    if args.applications:
        print("\nApplications and replies:")
        for item in applications:
            print(f"  [{item['status']:8s}] {item['company'] or '(unknown)':32s} {item['subject'][:60]}")
        return

    for job in jobs:
        print(f"  {job['title'][:46]:46s} | {job['company'][:24]:24s} | {job['location'][:20]}")

    if args.json:
        Path("email_jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        Path("email_applications.json").write_text(
            json.dumps(applications, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nWrote email_jobs.json and email_applications.json")


if __name__ == "__main__":
    main()
