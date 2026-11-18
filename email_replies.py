#!/usr/bin/env python3
"""Watch the inbox for what actually happens after an application.

Distinct from email_alerts.py, which mines job ads. This one reads the mailbox
for one thing only: a reply from a person or an ATS about an application that was
already sent. When one arrives it does three things, in this order:

  1. Sends a Telegram message straight away, so an interview request is not
     discovered three days late.
  2. Moves the matching row in the sheet from "Applied" to Interview, Assessment
     or Rejected, and stamps the reply date.
  3. Appends the mail to the Inbox tab, so there is a record even after the
     status changes again.

Interview invitations are the reason this exists. A rejection can wait. An
invitation cannot, because somebody else is booking the slots.

Secrets, the same two the alert reader uses:
    EMAIL_USER, EMAIL_APP_PASSWORD

Usage:
    python email_replies.py --dry-run     show what it would do
    python email_replies.py --once        notify and update the sheet
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from email_alerts import _guess_company, fetch_alert_messages  # reuse the IMAP plumbing

STATE_FILE = Path(__file__).with_name("seen_replies.json")

# Senders whose mail is never a reply about an application. Job alerts go to the
# other file; these are newsletters and course marketing that mention words like
# "career" and "opportunity" and would otherwise be classified as interviews.
NOISE_SENDERS = [
    "jobalerts-noreply@linkedin.com", "notifications@mail.talenthop.com",
    "hello.tealhq.com", "couresera.org", "coursera.org", "us-learning.alison.com",
    "notifications.freelancer.com", "noreply@tradingview.com", "bitunix.com",
    "mg.flexjobs.com", "updates.haystackapp.io", "noreply@m.learn.coursera.org",
    "invitations@linkedin.com",
]

INTERVIEW = re.compile(
    r"(invite you to an interview|like to invite you|invitation to interview|"
    r"schedule (?:a|an|your) (?:call|interview|meeting|chat)|"
    r"book a (?:time|slot|call)|calendly|cal\.com|"
    r"availability for (?:a|an) (?:call|interview|meeting)|"
    r"(?:technical|final|first|introductory|screening) (?:interview|call|conversation)|"
    r"interview with (?:our|the|a) |"
    r"would like to (?:speak|talk|meet) with you|"
    r"next step (?:is|would be) (?:a|an|to)|"
    r"move (?:you|your application) forward|"
    r"happy to move forward|proceed with your application)",
    re.I,
)

ASSESSMENT = re.compile(
    r"(test (?:task|assignment|exercise)|take[- ]home|home assignment|"
    r"coding (?:challenge|test|exercise)|assessment|"
    r"practical exercise|technical task|case study)",
    re.I,
)

# LinkedIn puts the verdict in the tracking URL and leaves the body nearly empty,
# so a word-only reader sees nothing. This catches email_email_jobs_application_rejected_01.
LINKEDIN_VERDICT = re.compile(
    r"application[_-]rejected|jobs_application_rejected", re.I)

REJECTED = re.compile(
    r"(unfortunately|not (?:to )?(?:move|moving) forward|"
    r"will not be (?:moving|progressing|advancing)|"
    r"decided (?:not to proceed|to proceed with other)|"
    r"other candidates|another candidate|position has been filled|"
    r"no longer under consideration|not been (?:selected|successful)|"
    r"unsuccessful|regret to inform|"
    r"keep your (?:CV|resume|details) on file)",
    re.I,
)

APPLIED = re.compile(
    r"(your application (?:was|has been) (?:sent|submitted|received)|"
    r"(?:we|i) (?:have )?(?:received|got) your application|"
    r"application (?:successfully )?(?:received|submitted|confirmed)|"
    r"thank(?:s| you)? for (?:your application|applying|your interest)|"
    r"your application to )",
    re.I,
)

# Words that mean the mail is not about one application at all.
NOT_ABOUT_ONE_APPLICATION = re.compile(
    r"(job alert|new jobs? (?:for|posted)|recommended jobs|"
    r"newsletter|unsubscribe from marketing|webinar|course|"
    r"your job search|complete your profile)",
    re.I,
)


def is_noise(message) -> bool:
    sender = message["sender"]
    if any(noise in sender for noise in NOISE_SENDERS):
        return True
    text = f"{message['subject']} {message['body'][:600]}"
    return bool(NOT_ABOUT_ONE_APPLICATION.search(text))


def classify(message):
    """Returns (kind, company, subject) or None.

    Order matters. "Unfortunately we cannot invite you to an interview" contains
    both an interview phrase and a rejection phrase, and the rejection is the
    truth, so rejection is tested first.
    """
    text = f"{message['subject']}\n{message['body'][:3000]}"
    company = _guess_company(message)
    if LINKEDIN_VERDICT.search(text) or REJECTED.search(text):
        return "Rejected", company, message["subject"]
    if INTERVIEW.search(text):
        return "Interview", company, message["subject"]
    if ASSESSMENT.search(text):
        return "Assessment", company, message["subject"]
    if APPLIED.search(text):
        return "Applied", company, message["subject"]
    return None


def load_seen() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else data.keys())
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen)[-5000:], ensure_ascii=False, indent=1),
                          encoding="utf-8")


NOTIFY = {"Interview": "🎯 INTERVIEW", "Assessment": "🧪 TEST TASK",
          "Rejected": "❌ Rejected"}


def format_reply(kind, company, subject, when) -> str:
    headline = NOTIFY.get(kind, kind)
    lines = [headline, ""]
    if company:
        lines.append(f"Company: {company}")
    lines.append(f"Subject: {subject}")
    if when:
        lines.append(f"When: {when}")
    if kind == "Interview":
        lines.append("")
        lines.append("Reply today. Book the earliest slot offered.")
    elif kind == "Assessment":
        lines.append("")
        lines.append("Note the deadline before you start. Do not use AI assistance in "
                     "a live task; companies increasingly say so explicitly.")
    return "\n".join(lines)


def run(dry_run=False):
    seen = load_seen()
    results = []
    for message in fetch_alert_messages(days=21, limit=400):
        mid = message["mid"] or (message["sender"] + message["subject"] + message["date"])
        if mid in seen:
            continue
        if is_noise(message):
            seen.add(mid)
            continue
        verdict = classify(message)
        seen.add(mid)
        if not verdict:
            continue
        kind, company, subject = verdict
        results.append({"kind": kind, "company": company, "subject": subject,
                        "date": message["date"], "sender": message["sender"]})

    if not results:
        print("No new replies.")
        if not dry_run:
            save_seen(seen)
        return []

    print(f"{len(results)} new reply/replies:")
    for item in results:
        print(f"  [{item['kind']:10s}] {item['company'] or '(unknown)':30s} {item['subject'][:50]}")

    if dry_run:
        print("\nDry run, nothing notified and nothing written.")
        return results

    # Telegram first. An interview request is time critical and a sheet write can
    # wait, so the message goes out before anything that can fail.
    try:
        from job_radar import telegram_send
        for item in results:
            if item["kind"] in NOTIFY:
                telegram_send(format_reply(item["kind"], item["company"],
                                           item["subject"], item["date"]))
    except Exception as exc:
        print(f"Telegram notify failed: {type(exc).__name__}: {exc}")

    # Then the sheet.
    try:
        from job_sheet_sync import append_inbox, update_status
        for item in results:
            if not item["company"]:
                continue
            changed = update_status(item["company"], item["kind"], item["date"])
            print(f"  sheet: {item['company']} -> {item['kind']}"
                  f" ({'updated' if changed else 'no matching row'})")
        append_inbox(results)
    except Exception as exc:
        print(f"Sheet update failed: {type(exc).__name__}: {exc}")

    save_seen(seen)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"Reply watch failed: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
