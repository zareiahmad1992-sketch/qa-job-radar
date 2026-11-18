#!/usr/bin/env python3
"""reconcile.py - make the tracker agree with the mailbox.

The tracker is only worth keeping if it is true. Twice now it has said a job was
never applied for while the confirmation sat in the inbox: Broadridge on 15 Nov,
and CommIT on 12 Nov, which stayed wrong for six days and put a 92% Armenia role
back on the "to do" list when it had already been done.

The cause is structural rather than a slip. job_radar.py finds adverts, so the
sheet fills up with what is available, not with what was sent. Only the mailbox
knows what was sent. So this file reads the two and writes the difference down.

Two asymmetries it takes seriously:

  The mailbox can prove an application happened. Silence proves nothing, so a row
  that says Applied with no email behind it is left alone. Confirmations do get
  lost, and demoting those would be its own kind of lie.

  A rejection outranks an interview, and both outrank Applied. Nothing is ever
  downgraded except by a message that says so.

Usage:
    python reconcile.py --dry-run     list the disagreements, change nothing
    python reconcile.py --apply       write the corrections to the sheet
    python reconcile.py --selftest    run the offline checks, no network

Needs the same secrets as the other two: EMAIL_USER, EMAIL_APP_PASSWORD,
SPREADSHEET_ID, and either GOOGLE_SERVICE_ACCOUNT_JSON or the three OAuth vars.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

JOBS_RANGE = "Jobs!A:M"
STATUS_COLUMN = 9      # I
APPLIED_COLUMN = 10    # J
LINK_COLUMN = 11       # K
NOTES_COLUMN = 12      # L
LAST_REPLY_COLUMN = 13 # M

# How much each verdict is worth. Higher wins. Rejected and Interview sit level
# because they are both conclusions, and a rejection never gets overwritten.
RANK = {
    "": 0,
    "Duplicate": 0,
    "Skip": 0,
    "Incomplete": 1,
    "Unknown": 1,
    "Not applied": 1,
    "Applied": 2,
    "Assessment": 3,
    "Interview": 4,
    "Rejected": 4,
}

# Statuses that mean "this application is over", so nothing should touch them.
FINAL = {"Skip", "Duplicate"}

# The sheet holds the name the radar found; the mailbox holds the name the
# employer uses for itself. These are the pairs seen so far. Keys are compared
# after stripping everything but letters and digits, so casing and punctuation
# do not matter here.
ALIASES = [
    {"commit", "commitarmenia", "comeetcommit"},
    {"teamviewerarmenia", "teamviewer", "teamviewergermanygmbh", "teamviewer germany"},
    {"specialized", "sbcstromer", "sbc", "specializedbicycles"},
    {"thevault", "vault"},
    {"softconstruct", "betconstruct", "softconstructarmenia"},
    {"intetics", "inteticsnorreply"},
    {"boardwise", "boardwisetechnologies"},
    {"sperasoft", "sperasoftyerevan"},
    {"zeyro", "zeyrotechnologies"},
    {"fortra", "fortraarmenia", "helpsystems"},
    {"deutscheboerse", "deutschebörse", "dbag"},
    {"msd", "msdcz", "merck"},
]

# Subject lines that are the employer saying the form was never finished. This
# is a real trap: the sheet said TeamViewer was Applied, but the mail in the
# mailbox was the one asking to verify the email address to finish applying.
INCOMPLETE = re.compile(
    r"(complete (?:your |the )application|verify your email to complete|"
    r"finish your application|your application is not complete|"
    r"complete your application for)",
    re.I,
)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def company_key(name: str) -> str:
    """Letters and digits only, lowercased. 'CommIT' and 'COMMIT' collide here,
    and so do 'SBC (Stromer)' and 'SBC'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def same_company(sheet_name: str, mail_name: str) -> bool:
    """True when the two names are the same employer.

    A prefix match handles 'TeamViewer' against 'TeamViewer Armenia' and
    'Intetics' against the gateway address. It is deliberately not a fuzzy match:
    a wrong link is worse than a missed one, because it writes a status onto the
    wrong row and that is exactly the class of bug this file exists to fix.
    """
    a, b = company_key(sheet_name), company_key(mail_name)
    if not a or not b:
        return False
    if a == b:
        return True
    for group in ALIASES:
        if a in group and b in group:
            return True
    short, long = sorted((a, b), key=len)
    return len(short) >= 4 and long.startswith(short)


def iso_date(value: str) -> str:
    """A date the sheet can sort, from either an RFC 2822 header or ISO."""
    value = (value or "").strip()
    if not value:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value[:len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


# --------------------------------------------------------------------------- #
# Reading the mailbox
# --------------------------------------------------------------------------- #

def classify_receipt(subject: str, body: str, sender: str = "") -> str:
    """One of Incomplete, Rejected, Interview, Assessment, Applied, or ''.

    Rejection is tested before the rest for the same reason it is in
    email_replies.py: "unfortunately we cannot invite you to an interview"
    contains both phrases and only one of them is true.

    LinkedIn needs its own test. Its rejection mail carries a one-line body and
    puts the real verdict in the tracking URL, which reads
    email_email_jobs_application_rejected_01. Reading the words alone sees an
    empty body and gives up.
    """
    text = f"{subject}\n{body}"
    if INCOMPLETE.search(text):
        return "Incomplete"
    if re.search(r"application[_-]rejected|jobs_application_rejected", text, re.I):
        return "Rejected"
    if re.search(r"application[_-](?:viewed|sent_to|submitted)", text, re.I) \
            and re.search(r"jobs_application", text, re.I):
        return "Applied"
    if re.search(
        r"(unfortunately|not (?:to )?(?:move|moving) forward|"
        r"will not be (?:moving|progressing|advancing)|"
        r"decided (?:not to proceed|to proceed with other)|"
        r"other candidates|another candidate|position has been filled|"
        r"no longer under consideration|not been (?:selected|successful)|"
        r"unsuccessful|regret to inform|"
        r"pursue other applicants|not to move forward with your application)",
        text, re.I,
    ):
        return "Rejected"
    if re.search(
        r"(invite you to an interview|like to invite you|invitation to interview|"
        r"schedule (?:a|an|your) (?:call|interview|meeting|chat)|"
        r"calendly|cal\.com|availability for (?:a|an) (?:call|interview|meeting))",
        text, re.I,
    ):
        return "Interview"
    if re.search(
        r"(test (?:task|assignment|exercise)|take[- ]home|home assignment|"
        r"coding (?:challenge|test|exercise)|assessment)",
        text, re.I,
    ):
        return "Assessment"
    if re.search(
        r"(your application (?:was|has been) (?:sent|submitted|received)|"
        r"(?:received|got) your application|"
        r"(?:we|i|they) (?:have |has )?(?:received|got) your application|"
        r"application (?:successfully )?(?:received|submitted|confirmed)|"
        r"thank(?:s| you)? for (?:your application|applying|your interest)|"
        r"your application to )",
        text, re.I,
    ):
        return "Applied"
    return ""


# --------------------------------------------------------------------------- #
# The reconciliation itself
# --------------------------------------------------------------------------- #

def build_receipts(messages) -> dict:
    """Collapses messages into one record per employer.

    Returns {company_key: {"display", "best_kind", "first", "last",
                           "incomplete_only", "subjects"}}.
    """
    out = {}
    for message in messages:
        subject = message.get("subject", "")
        body = message.get("body", "")
        sender = message.get("sender", "")
        kind = classify_receipt(subject, body, sender)
        company = message.get("company", "")
        if not kind or not company:
            continue
        key = company_key(company)
        when = iso_date(message.get("date", ""))
        record = out.setdefault(key, {
            "display": company, "best_kind": "", "first": when, "last": when,
            "kinds": set(), "subjects": [],
        })
        record["kinds"].add(kind)
        record.setdefault("kind_dates", {}).setdefault(kind, []).append(when)
        record["subjects"].append(f"{when} {kind}: {subject}")
        if when:
            record["first"] = min(filter(None, [record["first"], when]))
            record["last"] = max(filter(None, [record["last"], when]))
        if RANK.get(kind, 0) > RANK.get(record["best_kind"], 0):
            record["best_kind"] = kind
    for record in out.values():
        # A pile of "please finish your application" mails and nothing else means
        # the application never landed, however many letters arrived.
        record["incomplete_only"] = record["kinds"] == {"Incomplete"}
        if record["incomplete_only"]:
            record["best_kind"] = "Incomplete"
    return out


def reconcile_rows(rows, receipts) -> list:
    """Compares each row with what the mailbox says. Returns a list of changes.

    Each change is a dict with a kind of:
      "status"  the row disagrees with the mailbox and the mailbox wins
      "fill"    the row was right but a date was missing
      "orphan"  an application exists in the mailbox with no row in the sheet
      "ask"     the mailbox knows nothing and the row admits it does not either
    """
    changes = []
    matched = set()

    for index, row in enumerate(rows[1:], start=2):
        if len(row) < 4:
            continue
        name = row[2]
        status = row[STATUS_COLUMN - 1].strip() if len(row) >= STATUS_COLUMN else ""
        if status in FINAL:
            continue

        record = None
        for key, candidate in receipts.items():
            if same_company(name, candidate["display"]):
                record = candidate
                matched.add(key)
                break
        if not record:
            if status == "Unknown":
                changes.append({
                    "kind": "ask", "row": index, "company": name,
                    "current": status, "proposed": status,
                    "why": "the sheet does not claim an application and the mailbox "
                           "has no confirmation either. Only you know.",
                })
            continue

        if record["incomplete_only"]:
            changes.append({
                "kind": "status", "row": index, "company": name,
                "current": status, "proposed": "Not applied",
                "mail_first": record["first"], "mail_last": record["last"],
                "why": "the only mail is a request to finish the application, so it "
                       "was started and never completed.",
            })
            continue

        best = record["best_kind"]
        applied = row[APPLIED_COLUMN - 1].strip() if len(row) >= APPLIED_COLUMN else ""
        verdict_dates = sorted(d for d in record.get("kind_dates", {}).get(best, []) if d)
        verdict_date = verdict_dates[-1] if verdict_dates else ""
        if best in ("Rejected", "Interview", "Assessment") and applied \
                and verdict_date and verdict_date < applied:
            changes.append({
                "kind": "ask", "row": index, "company": name,
                "current": status, "proposed": status,
                "why": f"the {best.lower()} mail is dated {verdict_date}, before this "
                       f"application went in on {applied}. It is about an earlier try, "
                       f"so the row stays open. Worth a look.",
            })
            continue
        if RANK.get(best, 0) > RANK.get(status, 0):
            changes.append({
                "kind": "status", "row": index, "company": name,
                "current": status, "proposed": best,
                "mail_first": record["first"], "mail_last": record["last"],
                "fill_applied": False,
                "why": f"the mailbox holds a confirmation dated {record['first']} "
                       f"and the sheet says {status or 'nothing'}.",
            })
        else:
            applied = row[APPLIED_COLUMN - 1].strip() if len(row) >= APPLIED_COLUMN else ""
            reply = row[LAST_REPLY_COLUMN - 1].strip() if len(row) >= LAST_REPLY_COLUMN else ""
            if not applied or not reply:
                changes.append({
                    "kind": "fill", "row": index, "company": name,
                    "current": status, "proposed": status,
                    "applied_on": applied or record["first"],
                    "last_reply": reply or record["last"],
                    "why": "status agrees, a date was blank.",
                })

    # Same employer AND same role on more than one row. The title has to be part of
    # the key: SoftConstruct has two genuinely different adverts open, and a rule
    # that only looks at the employer closes the second one as a duplicate of the
    # first. The row that already carries a real status is the live one.
    def duplicate_key(row):
        title = row[3] if len(row) > 3 else ""
        return (company_key(row[2]), company_key(title)[:40])

    by_role = {}
    for index, row in enumerate(rows[1:], start=2):
        if len(row) < 4 or row[STATUS_COLUMN - 1].strip() in FINAL:
            continue
        by_role.setdefault(duplicate_key(row), []).append(index)
    live = {}
    for key, indexes in by_role.items():
        if len(indexes) < 2:
            continue
        for index in indexes:
            status = rows[index - 1][STATUS_COLUMN - 1].strip()
            if RANK.get(status, 0) >= RANK["Applied"]:
                live[key] = index       # the newest row with a real status wins
    for index, row in enumerate(rows[1:], start=2):
        if len(row) < 4 or row[STATUS_COLUMN - 1].strip() in FINAL:
            continue
        key = duplicate_key(row)
        if key in live and live[key] != index:
            changes = [c for c in changes if c.get("row") != index]
            changes.append({
                "kind": "status", "row": index, "company": row[2],
                "current": row[STATUS_COLUMN - 1].strip(), "proposed": "Duplicate",
                "mail_first": "", "mail_last": "",
                "why": f"row {live[key]} already holds this application.",
            })

    for key, record in receipts.items():
        if key in matched or record["incomplete_only"]:
            continue
        changes.append({
            "kind": "orphan", "company": record["display"],
            "proposed": record["best_kind"], "mail_first": record["first"],
            "why": "a real application with no row in the sheet at all.",
        })
    return changes


# --------------------------------------------------------------------------- #
# Reporting and writing
# --------------------------------------------------------------------------- #

def report(changes) -> str:
    if not changes:
        return "The sheet and the mailbox agree. Nothing to do."
    order = {"status": 0, "orphan": 1, "ask": 2, "fill": 3}
    changes = sorted(changes, key=lambda c: (order.get(c["kind"], 9), -(c.get("row") or 0)))
    lines = []
    counts = {}
    for change in changes:
        counts[change["kind"]] = counts.get(change["kind"], 0) + 1
    lines.append(f"{len(changes)} disagreement(s): " +
                 ", ".join(f"{v} {k}" for k, v in counts.items()))

    headings = {
        "status": "WRONG STATUS - the mailbox is right",
        "orphan": "NOT IN THE SHEET - applied for, never recorded",
        "ask": "UNKNOWN - only you know",
        "fill": "BLANKS - status right, dates missing",
    }
    for kind in ("status", "orphan", "ask", "fill"):
        group = [c for c in changes if c["kind"] == kind]
        if not group:
            continue
        lines.append("")
        lines.append(f"{headings[kind]}")
        lines.append("-" * len(headings[kind]))
        for change in group:
            where = f"row {change['row']}" if change.get("row") else "no row"
            arrow = (f"{change['current'] or '(blank)'} -> {change['proposed']}"
                     if change["kind"] in ("status", "ask") else change["proposed"])
            lines.append(f"  {where:8s} {change['company'][:28]:28s} {arrow}")
            lines.append(f"           {change['why']}")
    return "\n".join(lines)


def as_updates(changes) -> list:
    """Turns changes into (range, values) pairs for the Sheets API.

    Only the cells that change are written. Earlier versions of the sync wrote a
    whole block starting at I and wiped the link and the notes that sat beside
    the status, which is how a row loses its advert URL.
    """
    updates = []
    for change in changes:
        if change["kind"] == "status" and change.get("row"):
            row = change["row"]
            new_status = change["proposed"]
            updates.append((f"Jobs!I{row}", [[new_status]]))
            if new_status == "Rejected":
                next_action = "Closed. See the rejection mail"
            elif new_status == "Duplicate":
                next_action = change["why"].capitalize()
            else:
                next_action = f"Confirmed by mail on {change.get('mail_first', '')}".strip()
            updates.append((f"Jobs!N{row}", [[next_action]]))
            if change.get("mail_last"):
                updates.append((f"Jobs!M{row}", [[change["mail_last"]]]))
            if change.get("mail_first") and change.get("fill_applied", True):
                updates.append((f"Jobs!J{row}", [[change["mail_first"]]]))
        elif change["kind"] == "fill" and change.get("row"):
            row = change["row"]
            updates.append((f"Jobs!J{row}", [[change.get("applied_on", "")]]))
            updates.append((f"Jobs!M{row}", [[change.get("last_reply", "")]]))
    return updates


def _jobs_rows():
    from job_sheet_sync import access_token, sheets_get
    import os
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    return sheets_get(access_token(), spreadsheet_id, JOBS_RANGE)


def _mailbox(days: int = 45, limit: int = 400):
    from email_alerts import fetch_alert_messages
    return fetch_alert_messages(days=days, limit=limit)


def run(apply_changes: bool = False, messages=None, rows=None) -> list:
    messages = messages if messages is not None else _mailbox()
    rows = rows if rows is not None else _jobs_rows()
    receipts = build_receipts(messages)
    changes = reconcile_rows(rows, receipts)
    print(report(changes))
    if not apply_changes:
        print("\nDry run. Nothing written. Run with --apply to write the corrections.")
        return changes
    import os
    from job_sheet_sync import access_token, _sheets_update
    token = access_token()
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    for sheet_range, values in as_updates(changes):
        _sheets_update(token, spreadsheet_id, sheet_range, values)
        print(f"  wrote {sheet_range} = {values[0][0]}")
    return changes


# --------------------------------------------------------------------------- #
# Offline checks
# --------------------------------------------------------------------------- #

def selftest() -> None:
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"  FAIL {name}: got {got!r}, wanted {want!r}")
        else:
            print(f"  ok   {name}")

    # The two real cases that broke the sheet.
    check("CommIT vs COMMIT", same_company("COMMIT", "CommIT"), True)
    check("The Vault matches itself", same_company("The Vault", "The Vault"), True)
    check("TeamViewer Armenia vs Germany", same_company("TeamViewer Armenia",
                                                       "TeamViewer Germany GmbH"), True)
    check("Specialized vs SBC alias", same_company("SBC (Stromer)", "Specialized"), True)
    check("Intetics matches itself", same_company("Intetics", "Intetics"), True)
    # Two short generic names that must not collide, or the wrong row gets written.
    check("MSD does not match MSDynamics", same_company("MSD", "MSDynamics"), False)
    check("empty never matches", same_company("", "CommIT"), False)

    # A confirmation beats "Not applied", which is the bug in one line.
    rows = [
        ["Date found", "Tier", "Company", "Job title", "Location", "Fit",
         "Source", "Salary", "Status", "Applied on", "Link", "Notes", "Last reply"],
        ["2026-11-16", "1", "CommIT", "QA", "Yerevan", "92", "LinkedIn", "",
         "Not applied", "", "", "send it", ""],
    ]
    messages = [{"subject": "We Got It: Thanks for applying for QA Engineer",
                 "body": "Thanks for applying", "sender": "no-reply@comm-it.comeet-notifications.com",
                 "date": "Thu, 12 Nov 2026 20:14:44 +0000", "company": "CommIT"}]
    changes = reconcile_rows(rows, build_receipts(messages))
    check("one change for CommIT", len(changes), 1)
    check("status corrected", changes[0]["proposed"], "Applied")
    check("corrected row", changes[0]["row"], 2)
    check("date taken from the mail", changes[0]["mail_first"], "2026-11-12")

    # Silence must not demote a row.
    rows2 = list(rows)
    rows2[1] = list(rows[1])
    rows2[1][8] = "Applied"
    rows2[1][9] = "2026-11-12"
    rows2[1][12] = "2026-11-12"
    check("silence writes nothing", reconcile_rows(rows2, {}), [])

    # An unfinished application is not an application.
    incomplete = [{"subject": "Complete the application for Senior QA Engineer",
                   "body": "Verify your email to complete the application",
                   "sender": "no-reply@teamviewer.teamtailor-mail.com",
                   "date": "Fri, 13 Nov 2026 12:13:40 +0000",
                   "company": "TeamViewer Armenia"}]
    rows3 = [[
        "h", "h", "h", "h", "h", "h", "h", "h", "Status", "a", "l", "n", "m"],
        ["d", "1", "TeamViewer Armenia", "Senior QA", "Yerevan", "80", "x", "",
         "Applied", "2026-11-13", "", "", "2026-11-13"],
    ]
    changes3 = reconcile_rows(rows3, build_receipts(incomplete))
    check("incomplete is caught", changes3[0]["proposed"], "Not applied")

    # A rejection must beat an earlier acknowledgement.
    both = [
        {"subject": "Thanks for applying", "body": "we received your application",
         "sender": "x@y.com", "date": "Mon, 16 Nov 2026 10:00:00 +0000",
         "company": "Looper Engineering"},
        {"subject": "Your application to Software Test Engineer at Looper Engineering",
         "body": "https://x?lipi=email_email_jobs_application_rejected_01",
         "sender": "jobs-noreply@linkedin.com",
         "date": "Mon, 16 Nov 2026 11:19:00 +0000", "company": "Looper Engineering"},
    ]
    merged = build_receipts(both)
    key = company_key("Looper Engineering")
    check("rejection wins over acknowledgement", merged[key]["best_kind"], "Rejected")
    check("LinkedIn verdict read from the URL", classify_receipt(
        "Your application to X at Y", "lipi=email_email_jobs_application_rejected_01"), "Rejected")
    # Without the tracking URL the same subject is the "application was sent"
    # notice, and Applied is the honest reading of it.
    check("LinkedIn sent-notice reads as Applied", classify_receipt(
        "Your application to X at Y", "This email was intended for Ahmad Zarei"), "Applied")
    check("the URL overrides the subject", classify_receipt(
        "Your application to X at Y",
        "This email was intended for Ahmad Zarei lipi=email_email_jobs_application_rejected_01"),
        "Rejected")

    # An old rejection must not close a live application. Broadridge was rejected
    # on 4 Nov for an earlier attempt and applied again on 15 Nov.
    stale = [
        {"subject": "Application Update", "body": "we decided to move forward with other candidates",
         "sender": "broadridge@myworkday.com", "date": "Wed, 4 Nov 2026 13:49:56 +0000",
         "company": "Broadridge"},
        {"subject": "your application was sent to Broadridge", "body": "sent",
         "sender": "jobs-noreply@linkedin.com", "date": "Sun, 15 Nov 2026 18:28:17 +0000",
         "company": "Broadridge"},
    ]
    rows4 = [[
        "h", "h", "h", "h", "h", "h", "h", "h", "Status", "a", "l", "n", "m"],
        ["d", "1", "Broadridge", "Automation Test Engineer", "Yerevan", "61", "x", "",
         "Applied", "2026-11-15", "", "", "2026-11-15"],
    ]
    changes4 = reconcile_rows(rows4, build_receipts(stale))
    check("old rejection does not close a live row", changes4[0]["kind"], "ask")
    check("and it stays Applied", changes4[0]["proposed"], "Applied")

    # Two different roles at one employer are not duplicates of each other.
    rows5 = [
        ["h", "h", "h", "h", "h", "h", "h", "h", "Status", "a", "l", "n", "m"],
        ["d", "1", "SoftConstruct", "Quality Assurance Engineer", "Yerevan", "90", "x", "",
         "Applied", "2026-11-13", "", "", "2026-11-13"],
        ["d", "1", "SoftConstruct", "AI Automation Engineer", "Yerevan", "61", "x", "",
         "Not applied", "", "", "read it", ""],
    ]
    changes5 = reconcile_rows(rows5, {})
    check("a second role is not a duplicate", changes5, [])

    # The same role twice is.
    rows6 = [
        ["h", "h", "h", "h", "h", "h", "h", "h", "Status", "a", "l", "n", "m"],
        ["d", "1", "The Vault", "QA Engineer - Manual & Automated Testing", "Y", "65", "x", "",
         "Not applied", "", "", "send", "Second role"],
        ["d", "1", "The Vault", "QA Engineer - Manual & Automated Testing", "Y", "88", "x", "",
         "Applied", "2026-11-18", "", "", "2026-11-18"],
    ]
    changes6 = reconcile_rows(rows6, {})
    dups = [c for c in changes6 if c["proposed"] == "Duplicate"]
    check("the same role twice is a duplicate", len(dups), 1)
    check("and the older row is the one closed", dups[0]["row"], 2)

    check("iso from RFC 2822", iso_date("Wed, 18 Nov 2026 22:17:14 +0000"), "2026-11-18")
    check("iso passthrough", iso_date("2026-11-18"), "2026-11-18")
    check("iso from junk", iso_date("sometime last week"), "")

    # The write must not touch the link or the notes.
    updates = as_updates([{"kind": "status", "row": 5, "company": "X",
                           "current": "Not applied", "proposed": "Applied",
                           "mail_first": "2026-11-12", "mail_last": "2026-11-12",
                           "why": "test"}])
    ranges = [r for r, _ in updates]
    check("link column never written", "Jobs!K5" in ranges, False)
    check("notes column never written", "Jobs!L5" in ranges, False)

    print()
    if fails:
        print("\n".join(fails))
        raise SystemExit(f"{len(fails)} check(s) failed")
    print("all checks passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return
    try:
        run(apply_changes=args.apply)
    except Exception as exc:
        print(f"Reconcile failed: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
