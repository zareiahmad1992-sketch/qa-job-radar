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
    "devices": "Tested the same builds across devices and OS versions, on real handsets "
               "and on emulators, and filed exactly what broke on which one.",
    "roles": "Tested role-based access: different user types, restricted screens and "
             "the approval chains where one role signs off before the next can act.",
    "test": "Tested role-based access: different user types, restricted screens and "
            "the approval chains where one role signs off before the next can act.",
}


def job_text(job) -> str:
    return f"{job.get('title','')} {job.get('description','')} {job.get('location','')}".lower()


# Word starts matter. A plain "in" test put "scenarios" in the mobile bucket
# because it ends in "ios", and shipped a resume bullet about testing iOS builds
# to a posting that never mentions a phone. Short tokens go through here and must
# start at a word boundary, so "scenarios" and "javascript" stop matching "ios"
# and "java" while "apis" still matches "api".
def _hit(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(word), text) is not None


def has(job, *words) -> bool:
    text = job_text(job)
    return any(_hit(text, w) for w in words)


def picked_bullets(job, base, limit=7):
    """Base bullets for the role, plus the ones this posting actually asks for."""
    bullets = list(base)
    extras = [
        ("sql", ("sql", "database", "data integrity")),
        ("mobile", ("android", "ios", "mobile")),
        ("compat", ("cross-browser", "cross browser", "compatibility", "devices")),
        ("perf", ("load testing", "performance testing", "jmeter", "scalability",
                  "load and performance", "stress test")),
        ("ai", ("ai ", "llm", "automation tool")),
        ("devices", ("emulator", "real devices", "device testing", "os versions",
                     "android studio", "xcode", "test devices")),
        ("roles", ("permission", "rbac", "roles", "access control")),
    ]
    for key, words in extras:
        if any(_hit(job_text(job), w) for w in words):
            extra = BULLET_LIBRARY[key]
            if extra not in bullets:
                bullets.append(extra)
    return bullets[:limit]


# --------------------------------------------------------------------------- #
# Cover letter blocks. Each is a finished paragraph, written once, chosen by rule.
# --------------------------------------------------------------------------- #

OPENERS = {
    "armenia_remote": "I am applying for {title} at {company}, which is listed as fully "
                      "remote in Armenia. I already live in Yerevan, so there is no "
                      "relocation to arrange and no permit to wait for, and a remote "
                      "contract is how I have worked with distributed teams for years.",

    "armenia_onsite": "I am applying for {title} at {company} in {location}. I live in "
                      "Yerevan, so there is no relocation and no permit to wait for, and I "
                      "can be in the office as often as the role needs. What I want to be "
                      "straight about is the gap: this posting asks for electronics and EDA "
                      "experience, which I do not have. What I do have is the part it lists "
                      "first, which is automation and improvement of an existing test frame"
                      "work, and five years of being the person who finds out why a test "
                      "failed rather than the person who files the failure.",
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
    "eu_permit": "I am applying for {title} at {company}, and the first thing worth "
                 "settling is the one line in the posting that decides it. It asks for "
                 "someone working from the European Union with a work permit, and I am in "
                 "Yerevan, so I have neither. If that is a hard requirement, this ends here "
                 "and I would rather say so in the first paragraph than three emails in. If "
                 "{company} can engage a contractor outside the EU for a fully remote role, I "
                 "would like to be considered, and I will be straightforward about the "
                 "onboarding that needs.",

    "remote_region_anywhere": "I am applying for {title} at {company}. The title names a "
                              "region and the benefits section says the freedom to work from "
                              "anywhere, so let me settle the practical part in one line rather "
                              "than three emails in. I am in Yerevan, outside the EU, and I work "
                              "as a contractor. If the team is European and the contract is "
                              "remote, that works from my side. If this has to be an EU "
                              "employment contract, tell me and I will not take up your time.",

    "onsite_ask": "I am applying for {title} at {company}, and I will be direct about "
                  "the one thing that decides this. The role is based in {location} and I "
                  "live in Yerevan, so I would need work authorization to take it. The "
                  "posting does not mention sponsorship, so I am asking rather than "
                  "assuming: if you can support a permit for this role, I would like to be "
                  "considered, and if you cannot, I understand and would rather not waste "
                  "your time or mine.",

    "onsite_sponsor": "I am applying for {title} at {company}. I am based in Yerevan "
                      "and would need visa support to work on site, which the posting "
                      "suggests is possible. I have said that up front because the "
                      "process takes months and you should know before we start.",
}

BODY_BLOCKS = {
    "manual": "The manual half of my week is real work, and I do not treat it as a stepping "
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

    "mobile_banking": "The posting describes mobile banking with APIs and microservices behind "
                      "it, which is the system I test now. At Dotin that means the mobile "
                      "flows on Android and iOS over the same services the web portal uses, "
                      "and the questions that matter are the ones at the seam: what the app "
                      "shows when a service returns a partial failure, what the record looks "
                      "like after a retry, and whether the balance the customer sees matches "
                      "the balance actually stored. I check the last of those in SQL, because "
                      "the screen and the database disagree more often than anyone expects.",

    "ai": "I use AI tools most days, for test design, edge-case ideas and reading logs. "
          "The part that matters is the checking: I validate every output against the "
          "real product before it goes into a test case, because a confidently wrong "
          "suggestion costs more time than no suggestion.",

    "investigation": "When something fails I do not stop at the UI. I go into DevTools, "
                     "the network tab, console logs and local storage, and on the backend "
                     "side into our ELK logs and trace IDs, until I can name the layer "
                     "that owns the defect. A report saying which service broke is worth "
                     "more than one saying the screen looked wrong.",

    "frameworks": "I have built and maintained the automation layer rather than only used "
                  "one. At Dotin that means the pytest fixtures, the page objects and the "
                  "helper code the rest of the suite sits on, plus deciding what belongs in "
                  "CI and what is not worth automating at all. At Taak I set up the Appium "
                  "side from nothing. I review test code the way I review tests: does this "
                  "assertion actually fail when the thing it covers breaks, and would the "
                  "failure message tell the next person where to look.",

    "frameworks_jenkins": "I have built and maintained the automation layer rather than only "
                          "used one. At Dotin that means the pytest fixtures, the page objects "
                          "and the helper code the rest of the suite sits on. The pipelines I "
                          "run those in are GitLab CI and Docker rather than Jenkins, which is "
                          "the same work with different YAML, and I have not claimed Jenkins "
                          "on my CV. At Taak I set up the Appium side from nothing. I review "
                          "test code the way I review tests: does this assertion fail when "
                          "the thing it covers breaks.",

    "ai_build": "The AI project is the half I would be growing into, so let me be exact "
                "about which half. Using coding agents to draft and extend tests is already "
                "part of my week, and most of that time goes on checking the output rather "
                "than accepting it: a generated test that asserts something plausible but "
                "wrong is worse than a missing one, because it will pass forever and tell "
                "nobody anything. What I have not done is take that from a habit into "
                "infrastructure other people use. That is the work I want, and I would "
                "start it from the checking side, which is the right side to start from.",

    "ai_product": "The AI angle is what caught my attention, and I want to be precise about "
                  "where I sit with it. I do not have a background in model evaluation or LLM "
                  "benchmarking. What I do have is the part that matters for shipping a product "
                  "like this: using coding agents to draft and extend tests, then checking "
                  "whether each generated test actually asserts the right behaviour rather than "
                  "something plausible. On a product whose output varies, that checking is most "
                  "of the job, and it is the habit I already have.",

    "data_product": "A fair share of my testing is on data rather than screens: building the "
                    "datasets a test needs, running end-to-end regression through the whole "
                    "flow, and then checking the rows that came out the other end rather than "
                    "only the response that arrived. On a data product, a run that completes "
                    "cleanly and produces the wrong records is the defect worth catching.",

    "backoffice": "The part of the posting I recognize best is the back-office list, which is "
                  "what I have been testing for four years without calling it that. At Dotin "
                  "that means internal admin panels where an agent works on someone else's "
                  "account: different user types, screens only one role can see, and approval "
                  "chains where one person's sign-off is the condition for the next person "
                  "acting. The questions that decide whether those systems are correct are not "
                  "visual. Does the wrong role see the button? Does the action still go through "
                  "after the permission is removed? Does the audit log record who did it, and "
                  "what the record looked like before? I check those in the database as well as "
                  "on the screen, because an access rule that holds in the UI can still be "
                  "missing on the endpoint behind it.",

    "automation_shift": "The automation shift you describe is a move I have already made once. "
                        "At Dotin the regression suite was a manual checklist someone walked "
                        "through before every release, and most of what I did over a year was "
                        "decide which of those checks were worth automating and then replace "
                        "them. The rule I use is the boring one: automate a check when it is "
                        "repeated, deterministic, and its failure would actually mean something. "
                        "Everything else stays manual, because a suite full of tests nobody "
                        "trusts gets ignored and then deleted. In practice that means API and "
                        "data checks first, since they are stable and catch the defects that "
                        "matter, then the UI paths a customer cannot avoid, and exploratory work "
                        "left to a person.",

    "mobile": "Mobile is the half of my testing where the device itself is the variable, "
              "and most of what goes wrong is not in the app logic. At Taak I tested "
              "Android and iOS builds by hand on real handsets alongside emulators, "
              "which is how you find the defects that only appear on one OS version or "
              "one screen size, and I built the automation for the flows the team "
              "repeated every sprint with Appium and Python. At Fara Resane before that "
              "it was Android and web builds across devices, filing exactly what broke "
              "on which one rather than reporting that the app was broken. The habits "
              "that carry over to any mobile team: reproduce on a second device before "
              "calling it a defect, attach the logcat output rather than describing it, "
              "and check the app against a slow or flaky connection, because the happy "
              "path on office wifi is not where users live.",

    "sole_qa": "The requirement I want to be straight about is the sole-QA one. I have "
               "not been the only QA on a product, so I will not claim it. What I have "
               "done is own a large slice of one: at Dotin I own the Playwright suite end "
               "to end, which means nobody else reviews my test code and nobody else "
               "decides what belongs in it. I write the test plans from the requirements, "
               "decide which checks are worth automating and which stay manual, and give "
               "the release recommendation when the suite is green but I do not trust it, "
               "including the times I have said do not ship. What changes when there is "
               "no one else is the second opinion, so the habit that would carry over is "
               "writing the reasoning down rather than keeping it: why this risk is "
               "accepted, why this gap is covered by a manual check, what I would test "
               "first if there were only two days. That documentation is also the thing "
               "that makes a new QA useful quickly when the project grows past one.",

    "flaky": "The line in the posting I know best is fixing flaky tests, because that is "
             "most of what keeping a suite alive actually is. My Playwright suite runs "
             "before every release, and the failures that used to eat my mornings were not "
             "defects: a test waiting on an animation that runs for a different length each "
             "time, an assertion on text that a translation had changed, date logic that "
             "passed in the morning and failed after midnight, and a shared record that two "
             "tests wrote to in whatever order they happened to run. What I do about it is "
             "less clever than it sounds. The test waits for the state it needs instead of a "
             "fixed number of seconds, the assertion names one thing rather than five, each "
             "test builds the data it needs instead of borrowing someone else's, and when a "
             "test is flaky it gets fixed or deleted the same week rather than marked skip "
             "and forgotten. On the mocking side I stub the service responses with known "
             "payloads so a UI test fails for UI reasons and not because a backend stage was "
             "restarting, and I keep those payloads in one place so changing a field is one "
             "edit rather than twenty.",

    "final_judgment": "The line in the posting that describes my job already is the final "
                      "human judgment layer. That is what a release gate is at a bank, and it "
                      "is the part of QA that does not delegate well. In practice it means "
                      "reading the test results rather than counting them, and being willing to "
                      "say that a green suite is not the same as a safe release, because the "
                      "suite only covers what somebody thought to write down. I have held that "
                      "line for releases where the answer was no, and the useful part of the "
                      "job was explaining why in a way the developer could act on rather than "
                      "treat as an obstacle. It also means knowing which two things to check "
                      "when there is one afternoon and forty tickets, and saying out loud what "
                      "is being left untested rather than quietly hoping.",

    "exploratory": "Deep exploratory testing is the part of the job I would defend against "
                   "anyone who thinks it means clicking around. It starts from a model of what "
                   "the system should do, then attacks the places the model is thinnest: the "
                   "state that should be impossible, the record with a field nobody filled in, "
                   "the user who does the steps out of order, the second attempt at something "
                   "that already succeeded. At Dotin that is where the defects that mattered "
                   "came from, not from the scripted cases. What keeps it from being random is "
                   "writing down what I expected before I looked, so a surprise can be told "
                   "apart from my own wrong assumption, and then reproducing it twice with "
                   "steps someone else can follow. Half the arguments about a bug are really "
                   "arguments about what the requirement said.",

    "enablement": "The enablement side is the one I would not have thought to put on a CV. "
                  "Most of what I write at Dotin is read by other people: the test plans the "
                  "team works from, the release notes, the checklist a new joiner follows in "
                  "week one, the written explanation of a defect that becomes a discussion "
                  "rather than an argument. I have walked developers and product people through "
                  "features well enough that they could explain them to someone else, which is "
                  "the only real test of whether documentation worked. On a two-person QA team "
                  "covering a large surface that is not a nice extra, it is how the coverage "
                  "survives you being on holiday.",

    "eda_gap": "The gap in this application is the electronics, and I would rather name it "
               "than let it be discovered. I have not worked with layout or schematic tools, "
               "verification flows, GDSII, LEF or DEF, and no amount of testing web portals "
               "and APIs teaches you what a corner case means inside an IC design. What I "
               "have done is the thing the posting puts at the top of the list: automation "
               "and improvement of an existing test framework. That work is the same in any "
               "domain. You read a suite you did not write, find where it is slow or wrong "
               "or silently passing, decide what deserves to be covered, and then make the "
               "failure messages good enough that the next person does not have to rerun "
               "everything by hand to understand them. I do that now in Python on UNIX with "
               "git and shell, and I have learned three scripting languages on the job when "
               "a project needed them, which the posting asks for directly.",

    "career_shift": "The honest version of this application is that it is a move, not a "
                     "match. My five years are in QA, and the engineering here is AI "
                     "infrastructure: agents, pipelines, services, SDKs. What I would bring on "
                     "day one is the part that usually goes wrong in systems like that, which "
                     "is not the code but the belief that it works. I have spent five years "
                     "being the person who does not accept that a thing is fine because it ran "
                     "once, and on an AI product whose output varies, that is most of the job. "
                     "Automation I already write daily in Python. Backend services and event "
                     "pipelines I have not built, and I would be learning them next to people "
                     "who have, which is the only way I want to learn them.",

    "guardrail": "The specific value I would add is knowing where AI output fails. Using "
                 "coding agents every day for a year teaches you the failure shapes: the test "
                 "that asserts something plausible but wrong and will pass forever, the "
                 "confident summary of a log window that missed the service that actually gave "
                 "up, the edge case nobody wrote down because the prompt did not mention it. I "
                 "caught those because the cost of missing one is a defect in a bank, so I "
                 "check rather than trust. An AI Center of Excellence shipping agents into "
                 "production needs that habit as much as it needs the people who build them.",

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

    "caveat_typescript": "One honest gap: TypeScript in production. I know the language "
                         "\u2014 types, interfaces, generics, the compiler \u2014 but my "
                         "Playwright work has been in JavaScript and I have not yet shipped a "
                         "suite in TypeScript. The transfer is tooling rather than testing.",

    "caveat_cloud": "One honest gap: I am still early on Azure DevOps, having worked in "
                    "GitLab CI. The pipeline concepts are the same and I would be "
                    "productive within a month, but I did not want to imply otherwise.",

    "caveat_latvian": "One practical thing, said early: the posting asks for spoken and written "
                      "Latvian, and I do not speak it. My working language is English, which "
                      "is where all my test documentation and defect reports are written, and "
                      "I have worked in English with distributed teams for years. If the role "
                      "genuinely needs Latvian for internal communication, I would rather you "
                      "know now.",

    "caveat_seniority_agentic": "The one thing that decides this application, said plainly: the "
                                "posting asks for eight or more years, with at least one year of "
                                "bringing agentic AI into a QA team's real workflow, and I have "
                                "five years with none of that second part. What I do have is the "
                                "half the role depends on. I own the Playwright and Selenium "
                                "suites end to end, I read a requirement and find the case that "
                                "breaks it, and I use coding agents every day while treating "
                                "their output as something to check rather than something to "
                                "trust. The posting says it wants someone sceptical about AI "
                                "output, and that part I can already do. If the eight years is a "
                                "line you cannot move, I understand, and I would rather say so "
                                "than take up an interview slot.",

    "caveat_charles_desktop": "Two gaps in the list, said plainly rather than left to be found. "
                              "Charles Proxy: I inspect traffic in DevTools and read our ELK logs, "
                              "and I have not used Charles itself as a proxy for a device. The "
                              "work is the same reasoning, intercepting a request and changing "
                              "one field, and I would be productive with it quickly. Desktop "
                              "application testing: my five years are web, mobile and API, so a "
                              "packaged desktop build is a new surface for me. If either is a "
                              "hard requirement from week one, I would rather you know now.",

    "caveat_armenian": "One thing that decides more than it looks: the posting lists Armenian "
                       "among the required qualifications and I do not speak it. My working "
                       "language is English, which is where every test plan, defect report and "
                       "review comment I write is written, and that is how I have worked with "
                       "distributed teams for years. I live in Yerevan, so I know what that "
                       "means day to day, and I would rather say plainly that today I cannot "
                       "offer Armenian for internal communication. If the role needs it from "
                       "the first week, I understand, and I would rather you know now than "
                       "find out at interview.",

    "caveat_ukrainian": "One thing I should say first, because it decides the rest: the "
                        "posting asks for Ukrainian at a professional level and I do not "
                        "speak it. My working languages are English, which is where every "
                        "test plan, defect report and review comment I write is written, and "
                        "Persian as a native speaker. If Ukrainian is what the team "
                        "communicates in day to day, this will not work and I would rather "
                        "say so now than have it come up at interview.",

    "caveat_mcp": "One honest gap on the AI side: MCP. I understand what it is for, a "
                  "standard way for a model to call tools and services instead of each "
                  "integration being hand wired, and I can follow a design conversation "
                  "about it. I have not built an MCP server and have not wired one into a "
                  "testing setup. That is the part I would be learning rather than bringing.",

    "caveat_queues": "One gap in the backend list, so it does not come up as a surprise. "
                     "Of the three the posting names, APIs and databases are where most of "
                     "my testing sits: Postman and Swagger on the request side, SQL on the "
                     "stored side, and trace IDs across services when a record ends up in a "
                     "state nobody expected. Message queues are the piece I have tested "
                     "around rather than operated directly. I have followed asynchronous "
                     "flows through logs to find where a message was consumed and what "
                     "happened to it, and I have not worked hands on with a broker such as "
                     "Kafka or RabbitMQ, reading topics, consumer groups and lag. If the "
                     "role needs that from day one, I would be learning it on the job.",

    "caveat_golang": "On Go: the posting lists basic knowledge of Golang as desirable and I "
                     "have not written it. I read it well enough to follow what a service is "
                     "doing, and I have picked a language up from nothing before when a project "
                     "needed one, which is how the Appium setup at Taak started. If the "
                     "technical interview involves reading Go, I would rather walk through it "
                     "honestly than have you find out that the syntax was not as familiar as "
                     "the CV implied.",

    "caveat_credit_risk": "One thing about the domain, so the fit is not oversold. I have tested "
                          "banking systems and their business rules, and I have not worked with "
                          "a credit decisioning engine or a scoring model. What I recognize in "
                          "this posting is the layer around that: checking that a rule fires "
                          "where it should and nowhere else, that the record left behind "
                          "matches the decision the customer was told about, and that a policy "
                          "change did not quietly alter a case nobody was looking at. Those are "
                          "the tests I already write. If the role leans more toward designing "
                          "risk strategy than toward verifying the implementation of it, I "
                          "would be learning that part on the job.",

    "caveat_java": "One thing worth saying about the stack: the posting asks for the ability "
                   "to review and contribute to automated tests written in Java or Kotlin. My "
                   "automation is Playwright and Selenium in Python and JavaScript, and I have "
                   "not written Java. I read code well enough to review what a test asserts, "
                   "and I have picked up a new language before fast enough to be useful in "
                   "weeks. But I would rather name the gap now than have it arrive as a "
                   "surprise in a technical interview.",

    "caveat_mobile_years": "One thing about the years, since the posting is specific and I "
                           "would rather be counted accurately than generously. It asks for "
                           "three years of mobile testing and the honest total is about "
                           "eighteen months: a year at Fara Resane on Android and web builds, "
                           "and three months at Taak on Android and iOS, where I also set up "
                           "the Appium automation. What that time did give me is the part that "
                           "takes longest to learn, which is knowing when the device is the "
                           "problem rather than the code: the defect that exists on one OS "
                           "version, the layout that breaks on one screen size, the flow that "
                           "only fails on a bad connection. If the three years is a line you "
                           "cannot move I understand, and I would rather say this now than "
                           "have it surface later.",

    "caveat_crypto": "One thing about the domain, so the fit is not oversold. I have not tested "
                     "a custody or wallet product, and I am not going to claim the threshold "
                     "signing or key share work is familiar, because reading about MPC is not "
                     "the same as having tested it. What carries over is the layer under it, "
                     "which is where this class of bug usually lives: what happens when a "
                     "signing or recovery step fails halfway, whether a retry can produce a "
                     "second effect, whether the record left behind matches what the customer "
                     "was told, and whether the policy that should have stopped an action "
                     "actually fired for the role that tried it. Those are banking questions "
                     "too, and they are the ones I have spent four years asking. The crypto "
                     "specifics I would learn from your team, and I would rather say that now.",

    "caveat_seniority": "One thing about the level: the posting asks for ten or more years and "
                        "I have five. What I have is depth rather than breadth: banking "
                        "systems where a missed edge case becomes a wrong number in a "
                        "customer's account, automation I own end to end rather than write and "
                        "hand over, and a habit of writing things down so the next person does "
                        "not have to ask me. If a principal role needs someone who has already "
                        "done that for a decade, I understand. If you would weigh a strong five "
                        "years against a harder problem than most, I would like the conversation.",

    "caveat_gcp_studying": "On cloud: my test automation runs against web, mobile and API "
                           "layers, but the environments have been managed by our DevOps "
                           "colleagues rather than built by me. That is the piece I have "
                           "started on rather than just noticed: I am working through the "
                           "Google Cloud Digital Leader path now, and I would rather say where "
                           "I actually am than have it surface in a technical interview.",
}


def pick_opener(job) -> str:
    tier = job.get("tier")
    location = job.get("location", "Yerevan")
    text_ = job_text(job)
    # A title that names a region while the benefits say "work from anywhere" is a
    # contradiction worth one sentence, not a guess.
    if any(w in text_ for w in ("work from anywhere", "freedom to work from anywhere",
                               "anywhere in the world")) and \
            any(w in text_ for w in ("remote europe", "remote eu", "europe remote")):
        return OPENERS["remote_region_anywhere"]
    if tier == 1:
        if any(w in (location or "").lower() for w in ("hybrid", "on-site", "onsite",
                                                       "office", "in person")):
            return OPENERS["armenia_onsite"]
        if "remote" in (location or "").lower():
            return OPENERS["armenia_remote"]
        return OPENERS["armenia"]
    if tier == 2:
        return OPENERS["remote_eu"]
    text_ = job_text(job)
    if tier in (2, 3) and any(w in text_ for w in ("work permit are required",
                                                   "work permit is required",
                                                   "must have a work permit",
                                                   "right to work in the eu",
                                                   "eu work permit")):
        return OPENERS["eu_permit"]
    if tier == 4:
        text = job_text(job)
        if any(w in text for w in ("sponsorship", "visa support", "relocation package",
                                   "work permit", "blue card", "we sponsor")):
            return OPENERS["onsite_sponsor"]
        return OPENERS["onsite_ask"]
    return OPENERS["remote_single"]


def is_mobile_first(job) -> bool:
    """A posting about mobile work, not one that mentions a phone in passing."""
    text = job_text(job)
    return (text.count("mobile") >= 4
            or any(_hit(text, w) for w in ("mobile testing", "mobile app",
                                           "mobile application", "ios and android",
                                           "android and ios")))


def pick_body(job):
    """Chooses the letter's body paragraphs.

    Order matters more than it looks. The first paragraph has to be the one that
    answers what the posting actually asks for, and the letter has to stay on one
    page, so the list is built by priority and then cut. When a specific AI block
    is chosen it replaces the generic one, because saying the same thing twice in
    different words is worse than saying it once.
    """
    text = job_text(job)
    chosen = []

    taken = set()

    def take(key):
        if key in BODY_BLOCKS and BODY_BLOCKS[key] not in chosen:
            chosen.append(BODY_BLOCKS[key])
            taken.add(key)

    # A posting that is mostly "replace this manual process with automation"
    # wants to hear that he has done exactly that once already. It leads,
    # because it is the single closest thing in his history to the job.
    shifting = any(w in text for w in (
        "intensive automation", "automation-heavy", "automation heavy",
        "replace repetitive", "semi-manual", "semi manual",
        "automate our current", "automation of our current"))
    manual_led = (any(w in text for w in ("exploratory testing", "manual and exploratory",
                                          "deep exploratory"))
                  and not shifting)
    # Back office, admin panels and auditing are the closest thing in his history
    # to a list of nice-to-haves, so when a posting names them it leads. The two
    # short automation paragraphs cannot both sit in a one-page letter, so the
    # generic one gives way to the shift story, which says more.
    backoffice = any(w in text for w in (
        "back-office", "back office", "admin panel", "internal admin", "audit log",
        "approval workflow", "approval flows", "enterprise application",
        "enterprise applications", "access permission", "role model"))
    if manual_led:
        take("exploratory")
    if shifting:
        take("automation_shift")
    if backoffice:
        take("backoffice")
    # On a mobile-first posting the mobile paragraph belongs directly after the
    # opener. Leaving it fourth meant the letter opened with generic automation
    # talk on a job whose entire subject is phones.
    if is_mobile_first(job):
        take("mobile")

    # Always first: what he does most of the time. For a role that is automation
    # first, that paragraph leads instead and the manual one steps aside, because
    # opening with "most of my week is manual" against a pure automation posting
    # reads as a mismatch he does not actually have.
    if job.get("lead") == "automation":
        if not shifting:
            take("automation")
        take("frameworks_jenkins" if ("jenkins" in text and "gitlab" not in text)
             else "frameworks")
    elif not manual_led:
        take("manual")
        if any(w in text for w in ("automat", "playwright", "selenium", "sdet", "ci/cd", "pipeline")):
            take("automation")
    if any(w in text for w in ("api", "rest", "postman", "integration", "contract test")):
        take("api")

    # Domain colour, only one of these, because two reads as padding. Skipped
    # entirely when the posting is an AI engineering job: there the career change is
    # the thing that has to be said, and the four-paragraph cap means the generic
    # paragraph would push it out.
    ai_job = job.get("focus") == "ai_engineering"
    if not ai_job:
        if any(w in text for w in ("mcp", "claude code", "codex", "ai-powered", "ai powered",
                                   "agentic", "ai/llm tools", "ai tools for quality")):
            take("ai_build")
        elif any(w in text for w in ("ai/ml", "large language model", "generative ai",
                                     "coding agent", "llm", "ai-assisted",
                                     "ai/llm tools", "ai tools for quality")):
            take("ai_product")
        elif any(w in text for w in ("ai ", "cursor", "claude", "copilot", "ai tools")):
            take("ai")
    if any(w in text for w in ("data pipeline", "data product", "dataset", "etl", "data science")):
        take("data_product")
    if any(w in text for w in ("game", "gaming", "igaming", "casino", "player",
                               "sportsbook", "betting")):
        take("gaming")
    if any(w in text for w in ("banking", "bank ", "banks", "fintech", "payment",
                               "transaction", "trading", "financial services",
                               "credit risk", "credit policy", "credit decision",
                               "credit score", "lending", "bnpl", "loan", "loans",
                               "installment", "installments", "anti-fraud",
                               "underwriting", "scoring model", "money app")):
        take("fintech")

    # Mobile banking is a different shape from generic mobile testing, so it gets
    # its own paragraph when the posting names both.
    if (any(w in text for w in ("mobile banking", "banking app", "mobile bank"))
            and any(w in text for w in ("mobile", "android", "ios"))):
        take("mobile_banking")

    # Platform-specific, only when the posting names the platform
    # A mobile-first posting gets the mobile paragraph early. One that mentions a
    # phone in passing does not, because it would push out something more central.
    if is_mobile_first(job):
        take("mobile")
    if any(_hit(text, w) for w in ("jmeter", "performance testing", "load testing",
                                   "load test", "scalab", "response time")):
        take("perf")
    # Flaky tests and mocking are a specific ask. A posting that names them is
    # saying the suite is unstable and they want someone who can fix that, which
    # is a different proposition from writing new tests.
    if any(_hit(text, w) for w in ("flaky", "flakiness", "mocking", "stubbing",
                                   "scalable test suite", "dynamic ui")):
        take("flaky")
    # A role that leads with manual and names exploratory testing explicitly, and
    # that puts a person at the release gate, is a different job from one that
    # wants a test suite built. Those two paragraphs belong ahead of the
    # automation one, because the posting says automation comes later.
    if manual_led:
        if any(w in text for w in ("final human judgment", "final human judgement",
                                   "release confidence", "high-volume release",
                                   "high volume release", "release readiness",
                                   "prioritize workloads")):
            take("final_judgment")
    # A role that names electronics, layout, schematic or EDA tools is a domain he has
    # not worked in, and the letter says so before anyone else has to ask.
    # Only terms that cannot mean anything else. A web product has a page layout, so
    # "layout" alone is not evidence of silicon, and two letters were damaged before
    # this was tightened.
    eda_terms = ("eda tool", "eda tools", "electronic design automation", "ic design",
                 "schematic capture", "gdsii", "verilog", "vlsi", "silicon",
                 "rtl design", "place and route", "analog design", "layout versus schematic")
    if any(w in text for w in eda_terms) or job.get("focus") == "eda":
        take("eda_gap")
    # An AI engineering post, as opposed to an AI product to test. The distinction
    # matters: one is a career change and the other is a change of subject.
    if job.get("focus") == "ai_engineering" or (
            any(w in text for w in ("ai agents", "agent workflows", "llm agent",
                                    "langchain", "react and tool-calling",
                                    "agentic systems", "ai center of excellence"))
            and any(w in text for w in ("fastapi", "django", "kafka", "rabbitmq",
                                        "airflow", "dagster", "postgresql", "mongodb"))):
        take("career_shift")
        if any(w in text for w in ("ai agent", "agent workflows", "llm", "agentic",
                                   "production", "observability", "reliable")):
            take("guardrail")
    # Writing docs, guides and walkthroughs for other people is a small ask with a
    # real answer in his history, so it is worth saying rather than skipping.
    if any(w in text for w in ("technical documentation", "user guides",
                               "video walkthroughs", "technical enablement",
                               "feature docs", "presenting features")):
        take("enablement")
    # "Sole QA" and "from scratch" are a different job from joining a team, and the
    # honest answer is the interesting one here.
    if any(w in text for w in ("sole qa", "only qa", "end-to-end ownership of qa",
                               "from scratch", "establishing qa processes",
                               "independently own")):
        take("sole_qa")

    # The paragraphs that answer this posting specifically go in front of the generic
    # ones. Placing them here rather than leaving it to the take order above was
    # necessary: automation, frameworks, api and data product are taken first and fill
    # all four slots, so a posting with a real gap had the honest paragraph cut off
    # the end of the letter.
    specific = [k for k in ("eda_gap", "career_shift", "guardrail") if k in taken]
    for key in reversed(specific):
        block = BODY_BLOCKS[key]
        if block in chosen:
            chosen.remove(block)
        chosen.insert(1 if chosen else 0, block)

    # Always last: how he works and investigates
    take("investigation")
    take("team")

    # Five paragraphs only fit a page when there are few of them long. This
    # posting's blocks are dense, and the opener is three lines, so four is the
    # honest cap here. A two-page letter reads as a draft.
    return chosen[:4]


def pick_gaps(job):
    """Returns at most two honest gap paragraphs, most central requirement first.

    Two is the limit on purpose. Three admissions in one letter reads as an apology
    rather than an application.
    """
    text = job_text(job)
    gaps = []

    # Hard skill requirements the posting names explicitly
    if "armenian" in text:
        gaps.append(CLOSERS["caveat_armenian"])
    if "ukrainian" in text:
        gaps.append(CLOSERS["caveat_ukrainian"])
    if "latvian" in text:
        gaps.append(CLOSERS["caveat_latvian"])
    if "mcp" in text or "model context protocol" in text:
        gaps.append(CLOSERS["caveat_mcp"])
    if "charles" in text:
        gaps.append(CLOSERS["caveat_charles_desktop"])
    if any(w in text for w in ("message queue", "message queues", "kafka", "rabbitmq",
                               "message broker", "event streaming")):
        gaps.append(CLOSERS["caveat_queues"])
    if any(w in text for w in ("golang", " go,", "go code", "golang,")):
        gaps.append(CLOSERS["caveat_golang"])
    # Word boundaries matter here. Plain "java" matched inside "javascript", so a
    # posting asking for TypeScript and JavaScript produced a paragraph apologising
    # for not knowing Java, a language it never mentioned.
    elif re.search(r"\bjava\b(?!\s*script)|\bkotlin\b|\bc#\b|\s\.net\b", text):
        # A technology merely listed in a stack is not the same as a technology the
        # role is built on. Only apologise for Java when the posting asks for it.
        if re.search(r"(written in|write|develop|code) [^.\n]{0,40}\bjava\b(?!\s*script)"
                     r"|\bjava\b(?!\s*script)[^.\n]{0,25}(required|must)", text):
            gaps.append(CLOSERS["caveat_java"])
    # A credit or risk decisioning role is a different domain from generic
    # fintech testing, and saying so early is worth more than a smooth letter.
    if any(w in text for w in ("credit decision", "credit policy", "credit risk",
                               "risk assessment", "scoring model", "decision-making strategy",
                               "decision making strategy", "underwriting")):
        gaps.append(CLOSERS["caveat_credit_risk"])
    if any(w in text for w in ("google cloud", "gcp", "public cloud")):
        gaps.append(CLOSERS["caveat_gcp_studying"])
    if "ranorex" in text:
        gaps.append(CLOSERS["caveat_ranorex"])
    # A mobile posting that names a years requirement he does not meet. Only when
    # they actually state the number, so a generic mobile role does not get an
    # apology it never asked for.
    if (text.count("mobile") >= 4
            and re.search(r"\b[2-5]\+?\s*(?:or more\s*)?years?[^.\n]{0,40}mobile", text)
            or re.search(r"mobile[^.\n]{0,40}\b[2-5]\+?\s*(?:or more\s*)?years", text)):
        gaps.append(CLOSERS["caveat_mobile_years"])
    if "typescript" in text:
        gaps.append(CLOSERS["caveat_typescript"])

    # Crypto custody, wallets and custody-adjacent work is a domain he has not
    # touched, and this class of company reads CVs carefully enough to notice.
    if any(w in text for w in ("custody", "crypto", "wallet", "blockchain",
                               "mpc", "threshold signing", "key share",
                               "seed phrase", "cold storage")):
        gaps.append(CLOSERS["caveat_crypto"])

    # Azure DevOps only matters when GitLab is not offered as the alternative
    if "azure devops" in text and "gitlab" not in text:
        gaps.append(CLOSERS["caveat_cloud"])

    # Seniority last, because it is the least useful admission when a hard skill
    # gap is already on the table.
    agentic = any(w in text for w in ("agentic", "ai agent", "multi-agent", "mcp server"))
    big_gap = any(w in text for w in ("8+ years", "eight or more years", "10 or more years",
                                     "ten or more years", "10+ years", "7+ years",
                                     "principal test", "principal quality"))
    if agentic and big_gap:
        gaps.append(CLOSERS["caveat_seniority_agentic"])
    elif big_gap:
        gaps.append(CLOSERS["caveat_seniority"])

    # On an agentic posting that also asks for many years, that one admission is
    # worth more than the tooling gaps, so it leads and the small ones fall away.
    if CLOSERS["caveat_seniority_agentic"] in gaps:
        return [CLOSERS["caveat_seniority_agentic"]]
    return gaps[:2]


COUNTRY_NAMES = {
    "poland": "Poland", "portugal": "Portugal", "spain": "Spain",
    "germany": "Germany", "netherlands": "the Netherlands",
    "romania": "Romania", "bulgaria": "Bulgaria", "estonia": "Estonia",
    "lithuania": "Lithuania", "latvia": "Latvia", "czech": "the Czech Republic",
    "czechia": "the Czech Republic", "slovakia": "Slovakia", "hungary": "Hungary",
    "croatia": "Croatia", "serbia": "Serbia", "greece": "Greece",
    "italy": "Italy", "france": "France", "belgium": "Belgium",
    "austria": "Austria", "sweden": "Sweden", "denmark": "Denmark",
    "finland": "Finland", "norway": "Norway", "switzerland": "Switzerland",
    "ireland": "Ireland", "ukraine": "Ukraine",
    "united kingdom": "the United Kingdom", "england": "the United Kingdom",
    "israel": "Israel", "turkey": "Turkey",
}


def country_from_job(job) -> str:
    text = f"{job.get('location','')} {' '.join(job.get('restrictions', []))}".lower()
    for key, name in COUNTRY_NAMES.items():
        if key in text:
            return name
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
    # Strip bracket noise like "(m/f/d)" or "(Remote)" but keep "(QA)", which is
    # the part of the title that actually describes the job.
    cleaned = re.sub(r"\(\s*(m/f/d|f/m/d|m/w/d|w/m/d|all genders|remote|hybrid|"
                     r"on-?site|full[- ]time|part[- ]time|contract|contractor|"
                     r"temporary|\d+\s*(?:months?|years?))\s*\)", "",
                     cleaned, flags=re.I).strip()
    if is_mobile_first(job):
        return f"{cleaned}  |  Mobile QA, Appium & Katalon, Android & iOS"
    if job.get("lead") == "automation":
        return f"{cleaned}  |  Python, Playwright, API Testing"
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
    summary = ("Five years in software QA, close to four of them at Dotin testing banking "
               "systems. I work both halves of the job: manual testing where the "
               "judgement matters, and the automation that removes the repeated work. ")
    # A posting that names its focus gets a summary about that focus. Guessing
    # from single words is how a back-office role ends up with a summary about
    # subscription billing.
    focus = job.get("focus")
    if focus == "eda":
        summary = ("Five years in software QA, close to four of them at Dotin testing banking "
                   "systems: web portals, APIs and backend services, with the Playwright and "
                   "Selenium suites running in GitLab CI before every release. What I would be "
                   "bringing less of here is the electronics. I have not worked with layout, "
                   "schematic or verification EDA tools, and I am not going to pretend a bank's "
                   "test suite is an IC design flow. What does carry across is the part the "
                   "posting puts first: automation and improvement of an existing test framework. "
                   "I have built the layer the rest of a suite sits on, decided what belongs in "
                   "CI and what does not, and spent most of my time understanding why a test "
                   "failed rather than filing the failure. UNIX, shell, git and Python are daily. ")
    if focus == "ai_engineering":
        summary = ("Five years in software QA at Dotin, close to four of them on banking systems, "
                   "with the automation framework as my own: Python, pytest fixtures, Playwright "
                   "and Selenium in GitLab CI, REST APIs checked against Swagger, SQL against the "
                   "database behind them. The AI half is where I have been working for the last "
                   "year: coding agents to draft and extend tests, prompt and context work on "
                   "real flows, and most of my effort going into checking the output rather than "
                   "trusting it. I am not a backend engineer and I would be moving sideways into "
                   "that, which is the move the posting is asking for. ")
    if focus == "manual_exploratory":
        summary = ("Five years in software QA, close to four of them at Dotin testing banking "
                   "systems. The half of the job I would keep if I could keep only one is the "
                   "manual one: reading a requirement and working out what happens on a "
                   "reversal, a retry or a partial failure before anyone writes code, then "
                   "attacking the places the specification is thinnest. I also own the "
                   "Playwright suite that covers the repeated paths, so the exploratory work "
                   "goes where a test cannot, and I hold the release recommendation on my area, "
                   "including the times it was no. I use coding agents daily for test design "
                   "and log reading, and check what they produce before trusting it. ")
    if focus == "backoffice":
        summary = ("Five years in software QA, close to four of them at Dotin testing banking "
                   "systems: customer portals, APIs and the internal admin panels staff work "
                   "in. Most of that is back office, which means role models, permissions, "
                   "approval chains and the audit log behind each action, checked in the "
                   "database as well as on the screen. I also own the Playwright suite and "
                   "keep it honest in CI, and I use coding agents daily while checking what "
                   "they produce. ")
    if has(job, "api", "rest", "postman"):
        summary += ("A large part of that is API testing in Postman and confirming the "
                    "result in SQL, because a correct response and correct data are not "
                    "the same thing. ")
    if has(job, "automat", "playwright") and focus != "backoffice":
        summary += ("I maintain the Playwright suite and keep it running in CI rather "
                    "than on someone's laptop. ")
    if job.get("tier") == 1 and "remote" in (job.get("location", "") or "").lower():
        summary += ("This role is fully remote and based in Armenia, where I already live, "
                    "so there is no relocation and no permit to wait for.")
    elif job.get("tier") == 1:
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
        automation_line = ("Automation: Playwright and Selenium WebDriver in Python and "
                           "JavaScript; Appium for Android; suites running in GitLab CI "
                           "and Docker")
        if has(job, "katalon"):
            automation_line = ("Automation: Playwright and Selenium WebDriver in Python and "
                               "JavaScript; Appium and Katalon Studio for mobile; suites "
                               "running in GitLab CI and Docker")
        skills.append(automation_line)
    if any(_hit(job_text(job), w) for w in ("android", "ios", "mobile app",
                                            "mobile testing", "mobile application")):
        skills.append("Mobile: Android and iOS across devices and OS versions, Android "
                      "Studio, ADB and logcat")
    if has(job, "ai", "llm", "ai tools", "generative ai", "coding agent"):
        skills.append("AI-assisted testing: coding agents used to draft and extend tests, "
                      "with every generated assertion checked against real product behaviour")
    skills.append("Investigation: DevTools, network inspection, Kibana and ELK logs, "
                  "KQL, trace ID correlation; Jira, Xray, TestRail, ISTQB CTFL certified")
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
    # The third Taak bullet is the only place Katalon appears, and a mobile
    # posting is asking about exactly that tool. Cutting it to keep the page short
    # dropped the single line that answered the question, so it is kept whenever
    # the posting names a framework from that job.
    taak = CORE_RESUME["taak"]
    if has(job, "katalon", "appium", "selenium"):
        taak = taak
    else:
        taak = taak[:2]
    for b in taak:
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

    # Only an explicit ask earns a line here. Listing every missing nicety is not
    # honesty, it is a cover letter in the wrong document.
    learning = []
    if has(job, "typescript"):
        learning.append("TypeScript \u2014 known and practised, not yet shipped in a "
                        "production suite")
    if has(job, "generative ai", "genai"):
        learning.append("Python integration for automated AI-assisted regression, "
                        "which is where the test suites are headed")
    learning.append("Google Cloud Digital Leader path, in progress (skills.google/paths/9)")
    for item in learning:
        story.append(Paragraph(item, bul, bulletText="•"))

    story.append(Paragraph("CERTIFICATIONS", head))

    story.append(HRFlowable(width="100%", thickness=0.7, color=NAVY, spaceAfter=3))
    story.append(Paragraph("ISTQB Certified Tester, Foundation Level (CTFL), SEMATEC (Iran)",
                           bul, bulletText="•"))
    story.append(Paragraph("API Testing, 15 Days of Postman (For Testers)",
                           bul, bulletText="•"))
    story.append(Paragraph("Google Cloud Digital Leader path, in progress "
                           "(skills.google/paths/9)", bul, bulletText="•"))

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
                            topMargin=0.62 * inch, bottomMargin=0.55 * inch,
                            leftMargin=0.95 * inch, rightMargin=0.95 * inch,
                            title=f"Ahmad Zarei - Cover Letter - {job.get('company','')}",
                            author="Ahmad Zarei")

    name = ParagraphStyle("n", fontName="Carlito-Bold", fontSize=15, leading=18,
                          textColor=NAVY, spaceAfter=2)
    sub = ParagraphStyle("s", fontName="Carlito", fontSize=9.5, leading=12,
                         textColor=GRAY, spaceAfter=1)
    meta = ParagraphStyle("m", fontName="Carlito", fontSize=9, leading=12,
                          textColor=GRAY, spaceAfter=2)
    body = ParagraphStyle("b", fontName="Carlito", fontSize=10, leading=14.1,
                          textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=7.2)

    company = (job.get("company") or "").strip()
    title = job.get("title", "QA Engineer")
    location = job.get("location", "Yerevan")
    greeting = f"Dear {company} team," if company else "Dear hiring team,"

    story = [
        Paragraph("<b>AHMAD ZAREI</b>", name),
        Paragraph("QA Engineer" + "  |  " + resume_subtitle(job).split("|")[1].strip(), sub),
        Paragraph(CONTACT, meta),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=NAVY),
        Spacer(1, 12),
        Paragraph(greeting, body),
    ]

    # The location field carries prose, not just a place: "Yerevan, Armenia. Hybrid
    # (remote/office)". A period in it used to split the opener into a second sentence
    # that started with the team name. Only the place belongs in a sentence.
    place = re.split(r"(?<=[a-z])[.]\s", location)[0].rstrip(".")
    opener = pick_opener(job).format(title=title, company=company or "your team",
                                     location=place, country=country_from_job(job))
    if not company:
        opener = opener.replace(" at your team", "")
    story.append(Paragraph(opener, body))

    seen_text = set()
    for block in pick_body(job):
        # Backstop: two blocks sharing a sentence reads as padding. Compare the
        # first sixty characters, which is enough to catch a repeated opener.
        head = block[:60]
        if head in seen_text:
            continue
        seen_text.add(head)
        story.append(Paragraph(block, body))

    gaps = pick_gaps(job)

    # One page is the rule, so measure rather than guess. Everything after the gap
    # paragraphs is optional and droppable in the order listed, because the CV already
    # prints the same portfolio link and spilling onto a second page reads as a draft.
    # Each paragraph is rebuilt for every attempt: reportlab paragraphs are stateful
    # once built, and reusing them produced an empty file the first time this was
    # written.
    def compose(n_gaps: int, optional: int) -> list:
        flow = list(story)
        flow += [Paragraph(g, body) for g in gaps[:n_gaps]]
        extras = [CLOSERS["portfolio"].format(portfolio=PORTFOLIO), CLOSERS["honest_ai"]]
        flow += [Paragraph(t, body) for t in extras[:optional]]
        flow += [Spacer(1, 2),
                 Paragraph("Best regards,", body),
                 Paragraph("<b>Ahmad Zarei</b>", body)]
        return flow

    def page_count(buf) -> int:
        """Counts pages without pymupdf.

        The workflow installs only reportlab and cryptography, so the old
        `import pymupdf` here raised ModuleNotFoundError on every single run, the
        fit check never ran, and every cover letter fell back to the plainest
        variant. Counting "/Type /Page" objects in the built PDF needs nothing
        beyond the standard library, and reportlab writes uncompressed object
        headers so the count is reliable.
        """
        data = buf.getvalue()
        if not data:
            return 1
        trimmed = data.rstrip()
        if not trimmed.endswith(b"%%EOF"):
            # reportlab finished the file; this is only a sanity guard.
            pass
        count = len(re.findall(rb"/Type\s*/Page[^s]", data))
        return max(count, 1)

    def fits(flow) -> bool:
        import io
        buf = io.BytesIO()
        probe = SimpleDocTemplate(buf, pagesize=letter,
                                  topMargin=0.62 * inch, bottomMargin=0.55 * inch,
                                  leftMargin=0.95 * inch, rightMargin=0.95 * inch)
        probe.build(flow)
        return page_count(buf) <= 1

    # Try the richest version first and give something up only when it does not fit.
    # Richest first, then give up one optional block at a time. If the measurement
    # itself blows up we keep trying smaller variants, and only then fall back -
    # an exception in the ruler must not decide the letter.
    candidate = None
    for n_gaps, optional in (
        (len(gaps), 2), (len(gaps), 1), (len(gaps), 0),
        (min(len(gaps), 1), 0), (0, 0),
    ):
        trial = compose(n_gaps, optional)
        try:
            if fits(trial):
                candidate = trial
                break
        except Exception as exc:
            print(f"  fit check could not measure ({type(exc).__name__}: {exc})")
            continue
    if candidate is None:
        candidate = compose(min(len(gaps), 1), 1)
        print("  fit check unavailable, used a middle-richness letter")

    doc.build(candidate)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

_TRANSLIT = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss", "é": "e", "è": "e", "ê": "e", "á": "a", "à": "a",
    "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ç": "c", "ø": "o", "å": "a",
})


def safe_name(text: str) -> str:
    """Filenames a person can read. Transliterates the accents that show up in
    European company names rather than dropping the letter."""
    text = (text or "job").translate(_TRANSLIT)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40]


def tailor(job, out_dir: Path):
    """Builds both PDFs. Returns (resume_path, letter_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    company = safe_name(job.get("company") or job.get("name_hint") or "job")
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
