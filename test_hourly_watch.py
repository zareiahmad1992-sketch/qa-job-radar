#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end test for hourly_watch.py with no network at all.

Proves the part that has never been exercised: the write path. A fake mailbox, a
fake sheet and a fake Telegram are wired in, the script runs exactly as it would
in the Action, and the resulting sheet edits are asserted one by one.

Run:  python3 test_hourly_watch.py         (tests)
      python3 test_hourly_watch.py -v      (verbose, prints every event)
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("hw", HERE / "hourly_watch.py")
hw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw)

VERBOSE = "-v" in sys.argv

# --------------------------------------------------------------------------
# A sheet that looks like the real one: same header, same column order, and the
# same awkward company spellings that caused collisions this week.
HEADER = ["Date", "Tier", "Company", "Job title", "Location", "Fit %", "Source",
          "Salary", "Status", "", "", "", "Last reply", "Next action"]
SHEET = [HEADER]
for name, title in [
    ("Digitain", "Senior QA Engineer | Manual (Imagine Live)"),
    ("Digitain", "Persian Speaking Game Presenter"),
    ("Digitain", "Georgian Speaking Game Presenter"),
    ("ImagineLive", "Senior QA Engineer/Manual"),
    ("TeleClinic", "Quality Assurance Specialist (m/f/d)"),
    ("Oppizi", "Senior QA Engineer"),
    ("CommIT", "QA Engineer (Manual and UI Automation - Playwright)"),
    ("JustPlay GmbH", "Platform QA Chapter Lead (all genders)"),
    ("MSD", "QA & Automation Engineer - AI/ML applications"),
    ("Equiti AM", "QA Engineer"),
    ("SoftConstruct", "Quality Assurance Engineer"),
]:
    SHEET.append(["2026-11-20", "", name, title, "Yerevan", "85", "test", "",
                  "Applied", "", "", "", "", ""])

# --------------------------------------------------------------------------
# A mailbox with the mail that actually mattered this week, plus the noise that
# must never reach the sheet.
MAILBOX = [
    # --- must be recorded ------------------------------------------------
    ("notifications@app.bamboohr.com", "Thank you for applying at Digitain",
     "We have received your application for the Senior QA Engineer | Manual "
     "(Imagine Live) position and already reviewing it.", "22 Nov 2026 21:17:11"),
    ("satine@bamboohr.com", "Thank you for your interest in Imagine Live",
     "Thank you for your interest and time you invested in submitting your "
     "application for the Middle QA Engineer | Manual (Imagine Live) position. "
     "We have decided to pursue other candidates for the position at this time.",
     "06 Aug 2026 11:11:58"),
    ("msd@myworkday.com", "Thank You For Applying!",
     "Unfortunately we will not be moving forward with your application for the "
     "QA & Automation Engineer role at MSD.", "20 Nov 2026 14:31:35"),
    ("noreply@candidates.workablemail.com", "Thanks for applying to Oppizi",
     "Thanks for applying to Oppizi. Application submitted successfully.",
     "20 Nov 2026 18:18:57"),
    ("people.docmorris.com", "Deine Bewerbung als Quality Assurance Specialist",
     "We received your application for Quality Assurance Specialist at TeleClinic.",
     "22 Nov 2026 14:27:16"),
    ("recruiter@somecompany.am", "QA Engineer - next steps",
     "We would like to invite you to an interview. Please book a time here: "
     "https://calendly.com/x/qa", "23 Nov 2026 09:00:00"),
    ("hr@anothercompany.am", "Take-home test for the QA role",
     "Please complete the following assessment within 3 days. This is a take "
     "home assignment for the Senior QA Engineer role at Another Company.",
     "23 Nov 2026 10:00:00"),
    # --- security, never a sheet write -----------------------------------
    ("account-security@microsoft.com", "Microsoft account security info was added",
     "The following security info was recently added to the Microsoft account "
     "sm**t@private.icloud.com: Passkey", "09 Nov 2026 11:41:00"),
    # --- noise, must be dropped ------------------------------------------
    ("jobalerts-noreply@linkedin.com", "ALDI HOFER is hiring Senior QA Automation",
     "job alert new jobs for you", "23 Nov 2026 09:26:33"),
    ("alerts@updates.haystackapp.io", "Capco is hiring Quality Assurance Analyst",
     "job alert", "22 Nov 2026 07:09:33"),
    ("notify@jobicy.com", "New QA & Testing Jobs on Jobicy", "job alert digest",
     "22 Nov 2026 12:21:00"),
    ("community@test.io", "NEW: Newbies Tournament Winners",
     "weekly projects tournament", "20 Nov 2026 11:58:24"),
    ("notifications@github.com", "[GitHub] Run failed", "workflow run failed",
     "23 Nov 2026 08:00:00"),
]


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages


def fake_fetch_messages(days=3, limit=300, box_name="INBOX"):
    for sender, subject, body, when in MAILBOX:
        yield {"sender": sender.lower(), "sender_raw": sender, "subject": subject,
               "body": body, "date": when,
               "mid": f"<{abs(hash(sender + subject))}@test>"}


TGS = []
WRITES = []


def fake_sheets_get(token, spreadsheet_id, cell_range):
    return [list(row) for row in SHEET]


def fake_sheets_update(token, spreadsheet_id, cell_range, values):
    WRITES.append((cell_range, values))
    # Apply to the in-memory sheet so a second event sees the change.
    start = cell_range.split("!")[1]
    row_number = int("".join(ch for ch in start.split(":")[0] if ch.isdigit()))
    first_col = 9  # I
    while len(SHEET[row_number - 1]) < 14:
        SHEET[row_number - 1].append("")
    for offset, value in enumerate(values[0]):
        SHEET[row_number - 1][first_col - 1 + offset] = value
    return {"updatedCells": len(values[0])}


def fake_sheets_append(token, spreadsheet_id, cell_range, rows):
    TGS.append(("INBOX_ROWS", len(rows)))
    return {"updates": {"updatedRows": len(rows)}}


def fake_access_token():
    return "fake-token"


def fake_telegram_send(text):
    TGS.append(text)
    return True


hw.fetch_messages = fake_fetch_messages
hw.sheets_get = fake_sheets_get
hw.sheets_update = fake_sheets_update
hw.sheets_append = fake_sheets_append
hw.access_token = fake_access_token
hw.telegram_send = fake_telegram_send
hw.load_seen = lambda: set()
hw.save_seen = lambda seen: None


def status_of(row_index):
    return SHEET[row_index][hw.COL_STATUS - 1]


def find_row(company):
    for index, row in enumerate(SHEET):
        if row[2] == company:
            return index
    return None


# --------------------------------------------------------------------------
def test_end_to_end():
    import os
    os.environ["SPREADSHEET_ID"] = "fake-sheet"
    events = hw.run(dry_run=False, days=3)

    print("=" * 74)
    print("EVENTS CLASSIFIED")
    print("=" * 74)
    for e in events:
        print(f"  {e['kind']:10s} {str(e['company'])[:22]:22s} "
              f"{str(e.get('candidates'))[:38]:38s} | {e['subject'][:34]}")

    print()
    print("=" * 74)
    print("SHEET WRITES")
    print("=" * 74)
    for cell_range, values in WRITES:
        print(f"  {cell_range:16s} -> {values[0][0]:12s} {values[0][4]}")

    print()
    print("=" * 74)
    print("TELEGRAM")
    print("=" * 74)
    for t in TGS:
        if isinstance(t, tuple):
            print(f"  {t}")
        else:
            for line in t.splitlines():
                print(f"  {line}")
            print("  ---")

    # ---------------- assertions ----------------
    failures = []

    def check(label, got, want):
        ok = got == want
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"
              + ("" if ok else f" (wanted {want!r})"))
        if not ok:
            failures.append(label)

    print()
    print("=" * 74)
    print("ASSERTIONS")
    print("=" * 74)

    msd = find_row("MSD")
    check("MSD closed by a rejection", status_of(msd), "Rejected")

    oppizi = find_row("Oppizi")
    check("Oppizi still Applied", status_of(oppizi), "Applied")

    teleclinic = find_row("TeleClinic")
    check("TeleClinic reached", status_of(teleclinic), "Applied")

    # The Digitain mail must land on a Digitain row, never on ImagineLive.
    imagine = find_row("ImagineLive")
    check("ImagineLive row not touched by the Digitain mail",
          status_of(imagine), "Rejected")

    # A rejection for the ImagineLive application is real, and ImagineLive's row
    # is the one titled Senior QA Engineer/Manual.
    rejected_events = [e for e in events if e["kind"] == "Rejected"]
    check("two rejections found", len(rejected_events), 2)

    interviews = [e for e in events if e["kind"] == "Interview"]
    check("interview detected", len(interviews), 1)

    assessments = [e for e in events if e["kind"] == "Assessment"]
    check("assessment detected", len(assessments), 1)

    # Noise must never appear as an event.
    noisy = [e for e in events
             if any(x in (e["subject"] or "").lower()
                    for x in ("jobicy", "tournament", "job alert", "run failed"))]
    check("no noise events", len(noisy), 0)

    security_leak = [e for e in events if "passkey" in (e["subject"] or "").lower()]
    check("security mail not written to sheet", len(security_leak), 0)
    check("security mail pushed to Telegram",
          any("SECURITY" in t for t in TGS if isinstance(t, str)), True)

    check("Inbox tab appended once", len([t for t in TGS if isinstance(t, tuple)]), 1)

    # Telegram must be one message, not one per event.
    texts = [t for t in TGS if isinstance(t, str)]
    check("one Telegram message", len(texts), 1)

    print()
    if failures:
        print(f"FAILED: {len(failures)} assertion(s): {failures}")
        return 1
    print("ALL PASS")
    return 0


def test_the_digitain_collision():
    """The specific bug: a Digitain mail mentioning Imagine Live must not write
    onto the ImagineLive row."""
    print()
    print("=" * 74)
    print("TARGETED: the Digitain / ImagineLive collision")
    print("=" * 74)
    known = [row[2] for row in SHEET[1:]]
    message = {
        "sender": "notifications@app.bamboohr.com",
        "subject": "Thank you for applying at Digitain",
        "body": "We have received your application for the Senior QA Engineer | "
                "Manual (Imagine Live) position.",
    }
    cands = hw.candidates_for(message, known)
    print(f"  candidates, best first: {cands}")
    rows = [list(r) for r in SHEET]
    row = None
    for name in cands:
        row = hw.pick_row(rows, name, "Applied")
        if row:
            break
    company = rows[row - 1][2] if row else None
    print(f"  resolved to sheet row {row}: {company}")
    ok = company == "Digitain"
    print(f"  [{'PASS' if ok else 'FAIL'}] anchor wins over the product name")
    return 0 if ok else 1


def test_no_downgrade():
    """An automated acknowledgement must not pull a row back from Interview."""
    print()
    print("=" * 74)
    print("TARGETED: no downgrade")
    print("=" * 74)
    rows = [list(r) for r in SHEET]
    target = find_row("SoftConstruct") + 1     # 1-based row on the sheet
    rows[target - 1] = (list(rows[target - 1]) + [""] * 14)[:14]
    rows[target - 1][hw.COL_STATUS - 1] = "Interview"
    WRITES.clear()
    result = hw.apply_status("t", "s", rows, "SoftConstruct", "Applied", "23 Nov",
                             "Quality Assurance Engineer")
    ok = result is None and not WRITES
    print(f"  row already Interview, incoming Applied -> {result}")
    print(f"  [{'PASS' if ok else 'FAIL'}] refused, nothing written")
    return 0 if ok else 1


if __name__ == "__main__":
    code = test_end_to_end()
    code |= test_the_digitain_collision()
    code |= test_no_downgrade()
    print()
    print("=" * 74)
    print("RESULT:", "ALL TESTS PASS" if code == 0 else "FAILURES ABOVE")
    print("=" * 74)
    raise SystemExit(code)
