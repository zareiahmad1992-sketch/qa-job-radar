#!/usr/bin/env python3
"""
tailor.py — build a resume and a cover letter for one job, as PDFs.

How this works, honestly. There is no language model here. The letters are
assembled from paragraphs I wrote by hand, and a rule engine picks which ones
belong based on what the posting actually says: Armenia or remote, manual or
automation, API-heavy or not, banking or gaming.

That means the output reads like a person wrote it, because a person did. It also
means it only covers the combinations in the block library below. A posting about
something genuinely unusual will get a more generic letter, which is still honest,
just less pointed.

Usage:
    python tailor.py --job job.json --out ./out
    python tailor.py --demo
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER

FONT_DIR = Path(__file__).with_name("fonts")
if not FONT_DIR.exists():
    FONT_DIR = Path(__file__).parent.parent / "fonts"

pdfmetrics.registerFont(TTFont("Carlito", str(FONT_DIR / "Carlito-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Carlito-Bold", str(FONT_DIR / "Carlito-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Carlito-Italic", str(FONT_DIR / "Carlito-Italic.ttf")))
registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold",
                   italic="Carlito-Italic", boldItalic="Carlito-Bold")

NAVY = HexColor("#1F5169")
DARK = HexColor("#222222")
GRAY = HexColor("#555555")

CONTACT = ("(+374) 098 462216   |   zarei.ahmad1992@gmail.com   |   "
           "linkedin.com/in/ahmad-zarei92   |   Yerevan, Armenia")
PORTFOLIO = "github.com/zareiahmad1992-sketch/qa-portfolio"


# --------------------------------------------------------------------------- #
# Facts about Ahmad. Nothing here is invented; every line is something he did.
# --------------------------------------------------------------------------- #

CORE_RESUME = {
    "dotin": [
        "Test banking systems across web portals, APIs and backend services, covering "
        "integration, end-to-end and UI layers.",
        "Wrote and maintained the Playwright suite for the customer portal and ran it "
        "before each release.",
        "Added automated API and regression tests to GitLab CI, which cut manual "
        "testing by around 40%.",
        "Used Postman, Swagger and SQL to check responses, business rules, status "
        "fields, timestamps and the data actually stored.",
        "Found 10+ critical defects before release and tracked 50+ defects in "
        "Jira/Xray through to verification.",
        "Traced failures across microservices with trace IDs and ELK logs to find the "
        "service that owned the defect.",
    ],
    "taak": [
        "Tested Android and iOS flows by hand on real devices and wrote up what broke.",
        "Built the mobile automation setup with Appium and Python for the flows we "
        "repeated every sprint.",
        "Automated about 75% of the regression suite with Selenium and Katalon Studio.",
    ],
    "fara": [
        "Tested Android and web builds by hand across devices and screen sizes.",
        "Wrote BDD/Gherkin scenarios from user stories and reviewed them with the team.",
        "Kept defect reports in Jira and Trello with clear steps and screenshots.",
    ],
}

# Extra bullets that only appear when the job asks for them.
BULLET_LIBRARY = {
    "api": "Tested REST endpoints in Postman with a separate environment per stage, "
           "validating status codes, payloads and business rules against Swagger.",
    "sql": "Checked stored data in SQL when a screen looked right but the numbers did "
           "not add up, which is where most of the quiet defects live.",
    "mobile": "Tested Android and iOS builds across devices and OS versions, with "
              "Android Studio, ADB and logcat for crash logs.",
    "compat": "Ran compatibility passes across browsers, devices and screen sizes and "
              "filed exactly what broke on which one.",
    "perf": "Ran JMeter load and response-time checks before busy releases to catch "
            "slow endpoints early.",
    "ai": "Used AI tools most days for test design, edge cases and log reading, then "
          "checked every result against the product before relying on it.",
    "roles": "Tested role-based access: different user types, restricted screens and "
             "the approval chains where one role signs off before the next can act.",
    "test": "Tested role-based access: different user types, restricted screens and "
            "the approval chains where one role signs off before the next can act.",
}


def job_text(job) -> str:
    return f"{job.get('title','')} {job.get('description','')} {job.get('location','')}".lower()


def has(job, *words) -> bool:
    text = job_text(job)
    return any(w in text for w in words)


def picked_bullets(job, base, limit=7):
    """Base bullets for the role, plus the ones this posting actually asks for."""
    bullets = list(base)
    extras = [
        ("api", ("api", "rest", "postman", "swagger")),
        ("sql", ("sql", "database", "data integrity")),
        ("mobile", ("android", "ios", "mobile")),
        ("compat", ("cross-browser", "cross browser", "compatibility", "devices")),
        ("perf", ("performance", "load test", "jmeter", "scalab")),
        ("ai", ("ai ", "llm", "automation tool")),
        ("roles", ("permission", "rbac", "roles", "access control")),
    ]
    for key, words in extras:
        if any(w in job_text(job) for w in words):
            extra = BULLET_LIBRARY[key]
            if extra not in bullets:
                bullets.append(extra)
    return bullets[:limit]


# --------------------------------------------------------------------------- #
# Cover letter blocks. Each is a finished paragraph, written once, chosen by rule.
# --------------------------------------------------------------------------- #

OPENERS = {
    "armenia": "I saw this role in {location} and I am applying. I live in Yerevan, so "
               "there is no relocation to arrange and I can start without waiting for "
               "a permit.",
    "remote_eu": "I am applying for {title} at {company}. I am based in Yerevan and I "
                 "have worked remotely with distributed teams for years, so a remote "
                 "contract is a normal way of working for me, not an adjustment.",
    "remote_single": "I am applying for {title} at {company}. I noticed the posting "
                     "names {country}, so before anything else: I am based in Yerevan "
                     "and would work as a contractor. If that does not fit, I would "
                     "rather know now than waste your time.",
    "onsite_sponsor": "I am applying for {title} at {company}. I am based in Yerevan "
                      "and would need visa support to work on site, which the posting "
                      "suggests is possible. I have said that up front because the "
                      "process takes months and you should know before we start.",
}

BODY_BLOCKS = {
    "manual": "Most of my week is still manual, and I do not treat that as a stepping "
              "stone. At Dotin I write the test plans and cases from the requirements, "
              "work through them with boundary value, equivalence partitioning and state "
              "transitions rather than clicking whatever the screen shows, and make sure "
              "every customer use case is covered before a release.",

    "automation": "On the automation side I own the Playwright and Selenium suites. What "
                  "that means in practice is less about writing new tests and more about "
                  "keeping the existing ones honest: checking what runs in CI, working out "
                  "why something went red, and fixing the test when the test was the "
                  "problem. A suite nobody trusts is worse than no suite.",

    "api": "A fair amount of my work is API testing in Postman: collections with a "
           "separate environment per stage, request and response validation against "
           "Swagger, and then SQL to confirm what actually landed in the database. A "
           "200 response and correct data are two different things.",

    "fintech": "The systems I test are banking systems, where a missed edge case becomes "
               "a wrong number in a customer's account. That sets the bar for how I read "
               "requirements: I look for what happens on a reversal, a retry, a partial "
               "failure, and what the record looks like afterwards.",

    "gaming": "Your products are ones people spend their free time in, which is a "
              "different kind of pressure from a banking back office. The habits carry "
              "over: trace the data, reproduce it twice, write it so someone else can "
              "follow, then check the fix did not break the screen next door.",

    "ai": "I use AI tools most days, for test design, edge-case ideas and reading logs. "
          "The part that matters is the checking: I validate every output against the "
          "real product before it goes into a test case, because a confidently wrong "
          "suggestion costs more time than no suggestion.",

    "investigation": "When something fails I do not stop at the UI. I go into DevTools, "
                     "the network tab, console logs and local storage, and on the backend "
                     "side into our ELK logs and trace IDs, until I can name the layer "
                     "that owns the defect. A report saying which service broke is worth "
                     "more than one saying the screen looked wrong.",

    "team": "I work in Scrum, review requirements with developers while they can still "
            "be changed, and write the documentation the rest of the team then works "
            "from. I am ISTQB CTFL certified and I work in English with distributed "
            "teams.",
}

CLOSERS = {
    "portfolio": "You can look at my work before we speak. {portfolio} has five "
                 "reproducible bug reports with screenshots, a checkout test plan, and a "
                 "Playwright suite where the known defects are marked xfail with the "
                 "reason attached.",

    "honest_ai": "One thing worth saying: the portfolio's first version of one bug "
                 "report overstated what was broken, and I rewrote it. A report that "
                 "claims too much is worse than no report, because the developer fixes "
                 "the wrong thing.",

    "caveat_ranorex": "One honest gap: Ranorex. I have not used it and did not put it on "
                      "my CV. My automation is Playwright and Selenium, which is the same "
                      "page-object thinking with a different library.",

    "caveat_typescript": "One honest gap: TypeScript at depth. My Playwright work has "
                         "been in Python and JavaScript, and I am moving to TypeScript now "
                         "rather than claiming years of it.",

    "caveat_cloud": "One honest gap: I am still early on Azure DevOps, having worked in "
                    "GitLab CI. The pipeline concepts are the same and I would be "
                    "productive within a month, but I did not want to imply otherwise.",
}


def pick_opener(job) -> str:
    tier = job.get("tier")
    location = job.get("location", "Yerevan")
    if tier == 1:
        return OPENERS["armenia"]
    if tier == 2:
        return OPENERS["remote_eu"]
    if tier == 4:
        return OPENERS["onsite_sponsor"]
    return OPENERS["remote_single"]


def pick_body(job):
    blocks = []
    blocks.append(BODY_BLOCKS["manual"])
    if has(job, "automat", "playwright", "selenium", "sdet", "ci/cd", "pipeline"):
        blocks.append(BODY_BLOCKS["automation"])
    if has(job, "api", "rest", "postman", "integration"):
        blocks.append(BODY_BLOCKS["api"])
    if has(job, "bank", "financ", "payment", "fintech", "transaction"):
        blocks.append(BODY_BLOCKS["fintech"])
    if has(job, "game", "gaming", "igaming", "casino", "player"):
        blocks.append(BODY_BLOCKS["gaming"])
    if has(job, "ai ", "llm", "ai-assisted", "ai tools", "cursor", "claude", "copilot"):
        blocks.append(BODY_BLOCKS["ai"])
    blocks.append(BODY_BLOCKS["investigation"])
    blocks.append(BODY_BLOCKS["team"])
    return blocks[:5]  # five paragraphs is the most that stays readable


def pick_gaps(job):
    gaps = []
    if "ranorex" in job_text(job):
        gaps.append(CLOSERS["caveat_ranorex"])
    if "typescript" in job_text(job):
        gaps.append(CLOSERS["caveat_typescript"])
    if "azure devops" in job_text(job):
        gaps.append(CLOSERS["caveat_cloud"])
    return gaps[:1]


def country_from_job(job) -> str:
    text = f"{job.get('location','')} {' '.join(job.get('restrictions', []))}".lower()
    for name in ("poland", "portugal", "spain", "germany", "netherlands", "romania",
                 "bulgaria", "estonia", "lithuania", "czech", "italy", "france"):
        if name in text:
            return name.title()
    return "one country"


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #

BANNED_TITLE_WORDS = {"senior", "junior", "middle", "lead", "principal", "sr", "jr"}


def resume_subtitle(job) -> str:
    """Mirrors the posting's own title words, without overstating his level."""
    title = job.get("title", "QA Engineer")
    words = [w for w in title.split() if w.lower() not in BANNED_TITLE_WORDS]
    cleaned = " ".join(words) or "QA Engineer"
    # Trim anything in brackets, which is usually noise like "(m/f/d)"
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()
    if "automat" in job_text(job):
        return f"{cleaned}  |  Manual & Automation"
    return f"{cleaned}  |  Manual, API & Mobile"


def build_resume(job, out_path: Path):
    doc = SimpleDocTemplate(str(out_path), pagesize=letter,
                            topMargin=0.42 * inch, bottomMargin=0.3 * inch,
                            leftMargin=0.47 * inch, rightMargin=0.47 * inch,
                            title=f"Ahmad Zarei - {job.get('title','QA Engineer')}",
                            author="Ahmad Zarei")

    name = ParagraphStyle("n", fontName="Carlito-Bold", fontSize=16, leading=19,
                          textColor=NAVY, alignment=TA_CENTER, spaceAfter=1)
    sub = ParagraphStyle("s", fontName="Carlito", fontSize=10, leading=12.5,
                         textColor=GRAY, alignment=TA_CENTER, spaceAfter=1)
    con = ParagraphStyle("c", fontName="Carlito", fontSize=8.5, leading=11,
                         textColor=DARK, alignment=TA_CENTER, spaceAfter=0)
    head = ParagraphStyle("h", fontName="Carlito-Bold", fontSize=9.6, leading=11.5,
                          textColor=NAVY, spaceBefore=7, spaceAfter=2)
    body = ParagraphStyle("b", fontName="Carlito", fontSize=9.1, leading=11.4,
                          textColor=DARK, spaceAfter=2)
    bul = ParagraphStyle("bu", fontName="Carlito", fontSize=9.1, leading=11.4,
                         textColor=DARK, leftIndent=11, bulletIndent=1,
                         bulletFontName="Carlito-Bold", bulletFontSize=9.1,
                         bulletColor=NAVY, spaceAfter=1.2)
    roleline = ParagraphStyle("r", fontName="Carlito-Bold", fontSize=9.1, leading=11.4,
                              textColor=NAVY, spaceBefore=3, spaceAfter=0.5)

    story = [
        Paragraph("AHMAD ZAREI", name),
        Paragraph(resume_subtitle(job), sub),
        Paragraph(CONTACT + "   |   English B2, Persian native", con),
        Spacer(1, 5),
        HRFlowable(width="100%", thickness=1.1, color=NAVY),
        Spacer(1, 2),
    ]

    # Summary, written per job
    story.append(Paragraph("SUMMARY", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    years = "Five years" if job.get("tier") == 1 else "Five years"
    summary = (f"{years} in software QA, close to four of them at Dotin testing banking "
               f"systems. I work both halves of the job: manual testing where the "
               f"judgement matters, and the automation that removes the repeated work. ")
    if has(job, "api", "rest", "postman"):
        summary += ("A large part of that is API testing in Postman and confirming the "
                    "result in SQL, because a correct response and correct data are not "
                    "the same thing. ")
    if has(job, "automat", "playwright"):
        summary += ("I maintain the Playwright suite and keep it running in CI rather "
                    "than on someone's laptop. ")
    if job.get("tier") == 1:
        summary += "I am in Yerevan and available on site."
    else:
        summary += "I am based in Yerevan and work remotely with distributed teams."
    story.append(Paragraph(summary, body))

    # Skills, trimmed to what this posting asks about
    story.append(Paragraph("TECHNICAL SKILLS", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    skills = [
        "Testing: functional, regression, integration, smoke, exploratory, UI, API, "
        "mobile, compatibility, end-to-end",
        "Test design: plans, cases, checklists, boundary value analysis, equivalence "
        "partitioning, decision tables, state transitions",
    ]
    if has(job, "api", "rest", "postman", "swagger"):
        skills.append("API and data: Postman collections and environments, Swagger, "
                      "REST validation, SQL database checks")
    if has(job, "automat", "playwright", "selenium", "sdet", "ci"):
        skills.append("Automation: Playwright and Selenium WebDriver in Python and "
                      "JavaScript; Appium for Android; suites running in GitLab CI")
    if has(job, "android", "ios", "mobile"):
        skills.append("Mobile: Android and iOS across devices and OS versions, Android "
                      "Studio, ADB and logcat")
    if has(job, "ai", "llm", "ai tools"):
        skills.append("AI-assisted work: AI tools for test design and log reading, with "
                      "every output checked against the product")
    skills.append("Investigation: DevTools, network inspection, ELK logs, KQL, trace ID "
                  "correlation; Jira, Xray, TestRail, ISTQB CTFL certified")
    for s in skills:
        story.append(Paragraph(s, bul, bulletText="•"))

    # Experience
    story.append(Paragraph("WORK EXPERIENCE", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))

    story.append(Paragraph("Dotin Company  |  Software QA Engineer  |  10/2022 to Present",
                           roleline))
    for b in picked_bullets(job, CORE_RESUME["dotin"]):
        story.append(Paragraph(b, bul, bulletText="•"))

    story.append(Paragraph("Taak Group  |  Software QA Engineer  |  07/2022 to 10/2022",
                           roleline))
    for b in CORE_RESUME["taak"][:2]:
        story.append(Paragraph(b, bul, bulletText="•"))

    story.append(Paragraph("Fara Resane Soroush  |  Product Tester  |  06/2021 to 06/2022",
                           roleline))
    for b in CORE_RESUME["fara"][:2]:
        story.append(Paragraph(b, bul, bulletText="•"))

    # Public work, certifications, education
    story.append(Paragraph("PUBLIC WORK", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    story.append(Paragraph(
        f"{PORTFOLIO}: five reproducible bug reports with screenshots, a checkout test "
        f"plan, a release checklist, and a Playwright/pytest suite where the known "
        f"defects are marked xfail with the reason attached.", bul, bulletText="•"))

    story.append(Paragraph("CERTIFICATIONS", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    story.append(Paragraph("ISTQB Certified Tester, Foundation Level (CTFL), SEMATEC (Iran)",
                           bul, bulletText="•"))
    story.append(Paragraph("API Testing, 15 Days of Postman (For Testers)",
                           bul, bulletText="•"))

    story.append(Paragraph("EDUCATION", head))
    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    story.append(Paragraph("B.Sc. Computer Software Engineering, Azad University of "
                           "Mashhad, 2018 to 2021", bul, bulletText="•"))
    story.append(Paragraph("A.S. Computer Software Engineering, Azad University of "
                           "Mashhad, 2013 to 2016", bul, bulletText="•"))

    doc.build(story)


# --------------------------------------------------------------------------- #
# Cover letter
# --------------------------------------------------------------------------- #

def build_cover_letter(job, out_path: Path):
    doc = SimpleDocTemplate(str(out_path), pagesize=letter,
                            topMargin=0.72 * inch, bottomMargin=0.66 * inch,
                            leftMargin=0.95 * inch, rightMargin=0.95 * inch,
                            title=f"Ahmad Zarei - Cover Letter - {job.get('company','')}",
                            author="Ahmad Zarei")

    name = ParagraphStyle("n", fontName="Carlito-Bold", fontSize=15, leading=18,
                          textColor=NAVY, spaceAfter=2)
    sub = ParagraphStyle("s", fontName="Carlito", fontSize=9.5, leading=12,
                         textColor=GRAY, spaceAfter=1)
    meta = ParagraphStyle("m", fontName="Carlito", fontSize=9, leading=12,
                          textColor=GRAY, spaceAfter=2)
    body = ParagraphStyle("b", fontName="Carlito", fontSize=10, leading=14.2,
                          textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=9)

    company = job.get("company", "your team")
    title = job.get("title", "QA Engineer")
    location = job.get("location", "Yerevan")

    story = [
        Paragraph("<b>AHMAD ZAREI</b>", name),
        Paragraph("QA Engineer" + "  |  " + resume_subtitle(job).split("|")[1].strip(), sub),
        Paragraph(CONTACT, meta),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=NAVY),
        Spacer(1, 12),
        Paragraph(f"Dear {company} team,", body),
    ]

    opener = pick_opener(job).format(title=title, company=company, location=location,
                                     country=country_from_job(job))
    story.append(Paragraph(opener, body))

    for block in pick_body(job):
        story.append(Paragraph(block, body))

    for gap in pick_gaps(job):
        story.append(Paragraph(gap, body))

    story.append(Paragraph(CLOSERS["portfolio"].format(portfolio=PORTFOLIO), body))
    story.append(Paragraph(CLOSERS["honest_ai"], body))

    story.append(Spacer(1, 2))
    story.append(Paragraph("Best regards,", body))
    story.append(Paragraph("<b>Ahmad Zarei</b>", body))

    doc.build(story)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "job").strip("_")[:40]


def tailor(job, out_dir: Path):
    """Builds both PDFs. Returns (resume_path, letter_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    company = safe_name(job.get("company", "job"))
    resume_path = out_dir / f"Ahmad_Zarei_Resume_{company}.pdf"
    letter_path = out_dir / f"Ahmad_Zarei_CoverLetter_{company}.pdf"
    build_resume(job, resume_path)
    build_cover_letter(job, letter_path)
    return resume_path, letter_path


DEMO_JOB = {
    "title": "QA Automation Engineer",
    "company": "Redwerk",
    "location": "Remote",
    "tier": 2,
    "score": 97,
    "source": "Djinni",
    "url": "https://example.com",
    "description": ("We are looking for a QA Automation Engineer with Playwright and "
                    "API testing experience. You will write test plans, run regression "
                    "and integration testing, maintain automation in CI/CD, and test "
                    "REST APIs in Postman. SQL knowledge is required."),
    "restrictions": [],
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="path to a job JSON file")
    ap.add_argument("--out", default="./tailored", help="output folder")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo or not args.job:
        job = DEMO_JOB
    else:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))

    r, l = tailor(job, Path(args.out))
    print(r)
    print(l)
