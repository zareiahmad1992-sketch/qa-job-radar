#!/usr/bin/env python3
"""
QA Job Radar — software QA roles for a candidate based in Yerevan, Armenia.

Priority order for what gets sent:

  1   Armenia — on-site or remote, any employer
  2   Remote and genuinely open: worldwide, EMEA, Europe with no country named
  25  Germany — on-site or remote. He asked for Germany by name, both ways
  3   Remote, but one European country is named
  4   On-site elsewhere in Europe, and only when sponsorship or relocation is
      actually mentioned

Anything outside those rows is dropped, including the manufacturing and hardware
quality jobs that dominate the "quality engineer" search results, and anything
already in the Jobs tab of his sheet.

Usage:
    python job_radar.py --once        scan and send to Telegram
    python job_radar.py --dry-run     scan and print only
    python job_radar.py --report      print everything with its tier, no filtering

Environment:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path(__file__).with_name("seen_jobs.json")
USER_AGENT = "Mozilla/5.0 (compatible; QAJobRadar/2.0)"

# Per-run caps so a backlog never floods the chat. Armenia and remote-open roles are
# always worth reading; the single-country remote ones are a long tail, so only the
# strongest few go out each run.
# Remote and sponsored roles come first. Tier 2 (remote, genuinely open) and
# tier 3 (remote, one country named) get the most room; tier 4 is on-site in
# Europe and drops to the smallest share, because every one of those still needs
# a work permit that the posting rarely offers.
# Armenia was capped at 8 and was the row that flooded the chat, because the
# same dozen Yerevan employers repost the same roles. Four a run is enough to
# notice a new one; anything above that is repetition he has already seen.
#
# Germany gets its own row because Ahmad asked for it by name. It is the one
# market he named for BOTH on-site and remote, so it sits at priority 2.5,
# level with remote work, rather than waiting at the bottom with the other
# on-site countries.
MAX_PER_TIER = {1: 4, 2: 10, 25: 6, 3: 6, 4: 2}
MAX_PER_RUN = 18

# Priority rows, lowest first. 25 is Germany. It sits between the truly open
# remote roles and the ones that name a single country, because Germany both
# hires the most from outside the EU and is the one country he singled out.
TIER_ROWS = (1, 2, 25, 3, 4)


# --------------------------------------------------------------------------- #
# What counts as the job Ahmad actually does
# --------------------------------------------------------------------------- #

# The title has to say one of these. Checking the title and not the whole
# description is deliberate: a warehouse job whose text mentions "test" once
# should not get through.
# Titles that contain a QA word but are a different job entirely. "Marketing
# Automation Engineer" passed the title filter and reached the chat because the
# pattern only asked for "automation". These are the cases seen in the wild.
NOT_A_QA_TITLE = [
    "marketing automation", "salesforce", "hubspot", "email automation",
    "marketo", "crm automation", "marketing cloud", "seo", "content",
    "business development", "sales representative", "account executive",
    "data entry", "support engineer", "network engineer", "devops engineer",
    "solutions architect", "product marketing",
]

SOFTWARE_QA_TITLE_PATTERNS = [
    r"\bqa\b", r"\bq\.a\.\b", r"quality assurance", r"software tester",
    r"\btester\b", r"test engineer", r"\bsdet\b", r"test automation",
    r"automation (qa|tester|engineer)", r"\bqa (engineer|analyst|specialist|lead)\b",
    r"software quality", r"test analyst", r"manual test", r"qa automation",
    r"mobile qa", r"api (tester|qa)", r"quality engineer",
]

# Words that mean it is a different job wearing a similar title. This is the
# list that was missing before and let through Polaroid, Flex, Ypsomed and the
# Estonian certification role.
HARD_EXCLUDE = [
    # manufacturing / hardware quality
    "manufacturing", "ems ", " odm ", "supplier quality", "npi ", "pfmea",
    "spc ", "msa ", "gr&r", "grr", "cp/cpk", "cpk", "8d ", "8d problem",
    "capa", "ts16949", "iatf", "iso 13485", "iso 14971", "mdsap",
    "iso 9001", "iso 17025", "gmp", "fda ", "21 cfr", "medical device",
    "calibration", "injection molding", "injection moulding", "soldering",
    "smt ", "pcb", "hardware validation", "hardware quality", "mechanical",
    "electrical", "electronics", "plastics", "vibration", "thermal",
    "lean manufacturing", "six sigma", "kaizen", "supplier audits",
    "laboratory", "certification body", "product certification", "emc ",
    "iec ", "ce marking", "ul certification", "regulatory affairs",
    # defence and cleared work
    "defence", "defense", "security clearance", "sicherheitsüberprüfung",
    "anü", "arbeitnehmerüberlassung", "classified",
    # on-site outside the two regions we accept
    "must relocate to the us", "us work authorization", "must be based in the us",
    "only us residents", "w2 only", "authorized to work in the us",
    # Switzerland states work authorisation as an eligibility condition, and the
    # quota plus labour market test makes a permit for a QA role a non starter.
    # Swiss remote roles still get through, because this matches the permit line
    # rather than the country.
    "swiss/eu/efta work permit", "swiss work permit", "switzerland work permit",
    "eligibility to work in switzerland", "right to work in switzerland",
    "arbeit in der schweiz", "aufenthaltsbewilligung",
]

# Postings that need a language Ahmad does not speak. Kept narrow so a job that
# merely mentions a German client does not get dropped.
LANGUAGE_BLOCKERS = [
    "fluent german", "german required", "deutschkenntnisse", "sehr gute deutsch",
    "fließend deutsch", "verhandlungssicher deutsch", "deutsch (c1", "deutsch c1",
    "german (c1", "german c2", "c1 german", "c2 german", "b2 german required",
    # German requirements, including the phrasing German-language postings use.
    # "Sehr gutes Deutsch" was slipping through, so the whole family is listed now.
    "sehr gutes deutsch", "sehr gute deutsch", "gutes deutsch", "gute deutsch",
    "deutsch und", "deutsch in wort", "wort und schrift", "fliessendes deutsch",
    "fließendes deutsch", "deutsch fliessend", "deutsch fließend",
    "deutschkenntnisse", "deutsch c1", "deutsch c2", "deutsch b2",
    "german fluent", "fluent in german", "german speaker", "german-speaking",
    "nederlands", "dutch required", "fluent dutch", "estonian required",
    "fluent french", "french required", "fluent spanish required",
    "italiano madrelingua", "polski wymagany",
    # Demands for a native speaker of a language Ahmad does not have. These come
    # straight out of the Djinni listing fields, which is why they were missed.
    "ukrainian — native", "ukrainian native", "ukrainian - native",
    "russian — native", "russian native", "russian - native",
    "polish — native", "polish native", "polish - native",
    "native ukrainian", "native russian", "native polish",
    # CEFR codes. A posting asking for a language Ahmad does not speak at any
    # level is a dead end, and C1/C2 in that language is worse than dead.
    "ukrainian c1", "ukrainian c2", "ukrainian b2", "ukrainian b1",
    "russian c1", "russian c2", "russian b2",
    "polish c1", "polish c2", "polish b2",
    "german c1", "german c2", "french c1", "french c2",
    "dutch c1", "dutch c2", "spanish c1", "spanish c2",
    "ukrainian — c1", "ukrainian — c2", "russian — c1", "russian — c2",
    # English above his level. He is B2, so a posting demanding C1 or C2 will
    # filter him out in screening. Better to know before he spends the effort.
    "english c1", "english c2", "english — c1", "english — c2",
    "english (c1", "english (c2", "c1 english", "c2 english",
    "native english", "english native", "native-level english",
    # Nordic and Baltic languages. These kept arriving by hand from job boards
    # the radar does not crawl, so the phrasings are all listed here rather than
    # only the bare language name. The bare name is deliberately not blocked:
    # "we are a Norwegian company" is not a language requirement, while
    # "Norwegian and English, both written and verbal" is.
    "norwegian and english", "norwegian, both written", "in norwegian",
    "fluent norwegian", "norwegian required", "norwegian (", "norwegian c1",
    "norsk", "skandinavisk", "scandinavian language",
    "swedish and english", "in swedish", "fluent swedish", "swedish required",
    "svenska", "swedish (",
    "danish and english", "in danish", "fluent danish", "danish required",
    "dansk", "danish (",
    "finnish and english", "in finnish", "fluent finnish", "finnish required",
    "suomi", "finnish (",
    # Ordered pairs only: "and danish" alone would block a posting that mentions
    # danish pastries in the perks section, which is not a language requirement.
    "english and norwegian", "english and swedish", "english and danish",
    "english and finnish", "english and lithuanian", "english and latvian",
    "norwegian-speaking", "swedish-speaking", "danish-speaking",
    "finnish-speaking", "norwegian speaking", "swedish speaking",
    "icelandic", "icelandic required",
    "lithuanian required", "fluent lithuanian", "in lithuanian",
    "latvian required", "fluent latvian", "in latvian",
    "estonian required", "fluent estonian", "in estonian",
]

# Companies that ask applicants for money, sell training, or are otherwise not
# real employers. Seen in the wild; kept here so they never reach the chat again.
SCAM_BLOCKLIST = [
    "ace it careers", "meridian prime", "freelancer verified", "testerup",
    "tester up", "software testing academy", "it career switch",
    "experience factor", "training and placement", "pay for placement",
]

# Phrases that mean the "job" is really a course or a fee.
SCAM_PHRASES = [
    "paid training", "training fee", "placement fee", "registration fee",
    "security deposit", "purchase your own equipment", "unpaid training period",
    "investment required", "pay to start",
]

# Phrases that mean the employer will only hire in one named country. These are
# checked against the whole posting, and a match is fatal unless Armenia is named.
COUNTRY_LOCK_MARKERS = [
    "ukraine-based role", "ukraine based role", "located in ukraine",
    "located in kyiv", "located in lviv", "located in poltava", "located in kharkiv",
    "kyiv or poltava", "kyiv or lviv", "lviv or kyiv",
    "only consider candidates", "only considering candidates",
    "countries we consider candidates", "candidates located in",
    "we are looking for candidates located", "must be located in",
    "must be based in", "only candidates from", "only residents of",
    "us work authorization", "authorized to work in the us",
    "eu work permit required", "must have the right to work in the eu",
]

# A title written in these languages is a strong sign the interview is too.
FOREIGN_TITLE_HINTS = [
    "m/w/d", "(m/w/x)", "w/m/d", "mit", "und", "für", "entwickler",
    "tester (m", "referent", "spezialist", "ingenieur", "ingénieur",
    "desenvolvedor", "ingegnere", "inżynier", "vývojář",
    # German role words that mean the interview will be in German
    "softwaretester", "testingenieur", "qualitätssicherung",
    "testmanager", "testanalyst", "testerin",
]

QA_SKILLS = [
    # automation
    "playwright", "selenium", "cypress", "appium", "testcafe",
    # language / tools
    "python", "javascript", "typescript", "postman", "swagger", "sql",
    "rest", "graphql", "api testing", "json",
    # process
    "regression", "exploratory", "integration testing", "e2e", "manual",
    "test case", "test plan", "testrail", "jira", "xray", "zephyr",
    "istqb", "sdlc", "stlc", "agile", "scrum", "bdd", "gherkin",
    # infra
    "gitlab", "github actions", "jenkins", "docker", "kubernetes", "ci/cd",
    "jmeter", "k6", "elkj", "elk", "kibana", "grafana", "sql",
    "android", "ios", "mobile testing", "devtools",
]

# --------------------------------------------------------------------------- #
# Geography
#
# Ahmad lives in Yerevan. He has no EU work permit. That makes the realistic
# paths narrow, and they are not equal:
#
#   1  Armenia, on-site or remote        he can start next week, nothing to arrange
#   2  Remote, genuinely open            worldwide / EMEA / Europe with no single
#                                        country named. Contractor or EOR, workable
#   3  Remote, one European country      "Remote (Poland)" means they expect you in
#                                        Poland. Sometimes negotiable, often not
#   4  On-site Europe + sponsorship      real, but a visa takes months
#
# Anything else is dropped. A posting that will not hire outside a country Ahmad
# does not live in is worthless no matter how well the skills match.
# --------------------------------------------------------------------------- #

ARMENIA_MARKERS = ["armenia", "yerevan", "հայաստան", "ереван"]

# Remote wording that genuinely means "wherever you are".
REMOTE_WORLDWIDE = [
    "worldwide", "anywhere in the world", "anywhere", "any location", "any country",
    "work from anywhere", "globally distributed", "fully distributed", "global",
    "remote worldwide", "remote - worldwide", "remote, worldwide",
]

# Remote wording tied to a region Armenia sits inside or beside.
REMOTE_REGION = [
    "emea", "europe", "european union", "european economic area",
    "eastern europe", "central europe", "europe and", "europe, ", ", europe",
    "cis", "caucasus", "south caucasus", "eurasia", "mena",
    "middle east", "africa", "asia", "apac", "international",
]

# Countries whose remote roles are worth reading. Deliberately excludes Ukraine
# and Russia: the platforms that carry those postings almost always require a
# native speaker and residency, which the language and country-lock filters then
# spend the rest of their time rejecting.
EUROPE_COUNTRIES = [
    "albania", "andorra", "austria", "belgium", "bosnia", "bulgaria", "croatia",
    "cyprus", "czech", "czechia", "denmark", "estonia", "finland", "france",
    "germany", "greece", "hungary", "iceland", "ireland", "italy", "kosovo",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
    "monaco", "montenegro", "netherlands", "macedonia", "norway", "poland",
    "portugal", "romania", "san marino", "serbia", "slovakia", "slovenia",
    "spain", "sweden", "switzerland", "turkey", "united kingdom", "england",
    "scotland", "wales", "georgia", "azerbaijan",
]

# Cities that show up in location fields instead of the country name. Only used
# to decide whether an on-site role is in Europe; the country list above is still
# what handles remote restrictions.
EUROPE_CITIES = [
    "berlin", "munich", "hamburg", "frankfurt", "cologne", "stuttgart", "dusseldorf",
    "amsterdam", "rotterdam", "eindhoven", "brussels", "antwerp", "paris", "lyon",
    "madrid", "barcelona", "valencia", "lisbon", "porto", "milan", "rome", "turin",
    "warsaw", "krakow", "wroclaw", "prague", "brno", "bratislava", "budapest",
    "bucharest", "cluj", "sofia", "plovdiv", "zagreb", "belgrade", "ljubljana",
    "athens", "thessaloniki", "nicosia", "limassol", "valletta", "tallinn", "riga",
    "vilnius", "stockholm", "gothenburg", "oslo", "copenhagen", "helsinki", "espoo",
    "dublin", "cork", "london", "manchester", "edinburgh", "cambridge", "zurich",
    "geneva", "basel", "vienna", "graz", "tbilisi", "baku", "chisinau",
]

# The countries the radar actually searches on LinkedIn, in priority order.
# One entry per country, and it covers every country in EUROPE_COUNTRIES, so a
# country can no longer be recognised in a posting but never searched for.
# Order matters: contractor-friendly markets first, large Western markets next,
# small markets last, because LinkedIn throttles long runs and a throttle should
# cost the tail rather than the head.
SEARCH_COUNTRIES = [
    # Nearby and contractor-friendly. These hire from outside the country most
    # often, and they are the ones worth reading first.
    "Poland", "Portugal", "Romania", "Bulgaria", "Estonia", "Lithuania",
    "Latvia", "Czechia", "Slovakia", "Hungary", "Croatia", "Serbia", "Greece",
    "Georgia", "Moldova",
    # Large Western markets. More openings, but most want local residency.
    "Spain", "Netherlands", "Germany", "Ireland", "United Kingdom", "Austria",
    "Sweden", "Denmark", "Finland", "Norway", "Switzerland", "Italy", "France",
    "Belgium", "Luxembourg", "Cyprus", "Malta", "Slovenia", "Iceland",
    # Small or unusual markets. Fewer postings, cheap to include.
    "Albania", "Bosnia and Herzegovina", "Kosovo", "Montenegro",
    "North Macedonia", "Liechtenstein", "Monaco", "Andorra", "San Marino",
    "Turkey", "Azerbaijan",
]


# Employers worth searching by name as well as by country.
#
# Why both. LinkedIn's guest search does not filter by company: asking it for
# "QA SoftSwiss" returns Light & Wonder in Las Vegas and Wawa Inc in
# Pennsylvania, because it only matches keywords. So a company search is run and
# then every card whose company field does not contain the name is thrown away.
# Noisier than a real company filter, and still worth it, because these are
# employers that hire English-speaking QA and are already known to take people
# from outside their own country. The country searches stay exactly as they were.
WATCH_COMPANIES = [
    # Armenia
    "SoftConstruct", "Digitain", "BetConstruct", "ImagineLive", "Buymie",
    "Buildern", "TUMO", "Krisp", "PicsArt", "Sperasoft", "Synopsys",
    "Siemens EDA", "Broadridge", "Fortra", "ServiceTitan", "Tabby",
    # iGaming and gambling technology with English-speaking teams
    "SoftSwiss", "Evoplay", "Playson", "EveryMatrix", "Playtech", "Pragmatic Play",
    # European product and fintech companies that hire QA remotely
    "Bolt", "Wolt", "SumUp", "HelloFresh", "Personio", "JetBrains",
    "Grid Dynamics", "EPAM", "Luxoft", "Intellias", "Ciklum", "N-iX",
]

# LinkedIn locations that mean a region rather than one country. These catch
# postings that never name a country in the location field, which a per-country
# search walks straight past.
REGION_SEARCHES = [
    ("QA Engineer", "European Union"),
    ("QA Automation", "European Union"),
    ("SDET", "European Union"),
    ("QA Engineer", "EMEA"),
]

def linkedin_slot() -> int:
    """Which two-hour slot of the day this is, 0 to 11.

    Used to pick one shard per run, so the country and employer searches rotate
    instead of all firing at once.
    """
    return datetime.now(timezone.utc).hour // 2


def linkedin_shards():
    """The non-Armenian searches, grouped so one group fits in a single run.

    Returns a list of (name, queries). The order is by value: employers first,
    then contractor-friendly countries, then regions and sponsorship, then the
    larger Western markets split into three.
    """
    contractors = [c for c in SEARCH_COUNTRIES if c in CONTRACTOR_COUNTRIES]
    rest = [c for c in SEARCH_COUNTRIES if c not in CONTRACTOR_COUNTRIES]
    third = (len(rest) + 2) // 3
    half = (len(WATCH_COMPANIES) + 1) // 2
    return [
        ("employers A",
         [(f"QA {name}", "", 0, False, name) for name in WATCH_COMPANIES[:half]]),
        ("employers B",
         [(f"QA {name}", "", 0, False, name) for name in WATCH_COMPANIES[half:]]),
        ("contractor countries",
         [("QA Engineer", c, 0, True, None) for c in contractors]),
        ("regions and sponsorship",
         [(k, l, 0, True, None) for k, l in REGION_SEARCHES]
         + [(k, l, 0, True, None) for k, l in SPONSORSHIP_SEARCHES]),
        ("western markets A",
         [("QA Engineer", c, 0, True, None) for c in rest[:third]]),
        ("western markets B",
         [("QA Engineer", c, 0, True, None) for c in rest[third:2 * third]]),
        ("western markets C",
         [("QA Engineer", c, 0, True, None) for c in rest[2 * third:]]),
    ]


# A posting that offers sponsorship is worth as much as a remote one, but it never
# surfaces in a plain country search, because the words "visa" and "relocation" do
# not appear in a location field. These queries exist only to find those.
SPONSORSHIP_SEARCHES = [
    ("QA Engineer visa sponsorship", "Germany"),
    ("QA Automation Engineer", "Germany"),
    ("SDET", "Germany"),
    ("QA Engineer remote", "Germany"),
    ("Software Tester", "Germany"),
    ("QA Engineer visa sponsorship", "Netherlands"),
    ("QA Engineer visa sponsorship", "Poland"),
    ("QA Engineer visa sponsorship", "Romania"),
    ("QA Engineer visa sponsorship", "Czechia"),
    ("QA Test Engineer relocation package", "Portugal"),
    ("SDET visa sponsorship", "Spain"),
    ("QA Engineer work permit", "Ireland"),
]

# Short country codes used by APIs such as Landing.jobs and Djinni.
COUNTRY_CODES = {
    "at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr",
    "gr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt", "nl", "no",
    "pl", "pt", "ro", "se", "si", "sk", "gb", "uk", "ge", "md", "rs", "tr",
}

SPONSORSHIP_MARKERS = [
    "visa sponsorship", "sponsorship available", "we sponsor", "sponsor visa",
    "visa sponsored", "will sponsor", "sponsorship provided", "sponsorship offered",
    "work permit", "work visa", "relocation package", "relocation support",
    "relocation bonus", "paid relocation", "relocation assistance", "we relocate",
    "relocation offered", "relocation provided", "visa support", "visa assistance",
    "blue card", "eu blue card", "skilled worker visa", "tier 2",
    "certificate of sponsorship", "right to work support",
    # Phrasings that appeared in postings the first list walked past. All of these
    # mean the employer will handle the paperwork or the move.
    "relocation to", "we help with relocation", "we support your relocation",
    "we cover relocation", "we pay for relocation", "relocation is supported",
    "help you relocate", "assistance with relocation", "relocation help",
    "immigration support", "work authorization support", "we assist with visa",
    "visa and relocation", "relocation and visa", "we help with your move",
    "support your move", "help with documents", "residence permit",
]


# --------------------------------------------------------------------------- #
# Fetch helpers
# --------------------------------------------------------------------------- #

def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str):
    return json.loads(fetch(url).decode("utf-8", "replace"))


def clean_text(value) -> str:
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def as_list(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    return [v.strip() for v in str(value).split(",") if v.strip()]


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def job(source, title, company, location, url, description="", published="",
        remote=None, restrictions=None, relocation=False, tags=None):
    return {
        "source": source,
        "title": tidy_title(clean_text(title)),
        "company": clean_text(company) or "Not listed",
        "location": clean_text(location) or "Not specified",
        "url": html.unescape(str(url or "")).strip(),
        "description": clean_text(description)[:4000],
        "published": clean_text(published),
        "remote": remote,
        "restrictions": [r.lower() for r in as_list(restrictions)],
        "relocation": relocation,
        "tags": [str(t).lower() for t in as_list(tags)],
    }


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

def parse_himalayas():
    """Himalayas gives per-job location restrictions, which is the cleanest signal."""
    out = []
    for offset in (0, 20, 40):
        try:
            data = fetch_json(f"https://himalayas.app/jobs/api?limit=20&offset={offset}")
        except Exception:
            break
        for item in data.get("jobs", []):
            out.append(job(
                "Himalayas", item.get("title"), item.get("companyName"),
                ", ".join(as_list(item.get("locationRestrictions"))) or "Remote",
                item.get("applicationLink") or item.get("guid"),
                item.get("description"), item.get("pubDate"),
                remote=True, restrictions=item.get("locationRestrictions"),
            ))
    return out


def parse_remotive():
    data = fetch_json("https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
    out = []
    for item in data.get("jobs", []):
        out.append(job(
            "Remotive", item.get("title"), item.get("company_name"),
            item.get("candidate_required_location"), item.get("url"),
            item.get("description"), item.get("publication_date"),
            remote=True, restrictions=[item.get("candidate_required_location")],
        ))
    return out


def parse_jobicy():
    data = fetch_json("https://jobicy.com/api/v2/remote-jobs?count=50")
    out = []
    for item in data.get("jobs", []):
        out.append(job(
            "Jobicy", item.get("jobTitle"), item.get("companyName"),
            item.get("jobGeo") or "Remote",
            item.get("url") or item.get("jobUrl"),
            item.get("jobDescription"), item.get("pubDate"),
            remote=True, restrictions=[item.get("jobGeo")],
        ))
    return out


def parse_workingnomads():
    data = fetch_json("https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for item in data:
        out.append(job(
            "Working Nomads", item.get("title"), item.get("company_name"),
            item.get("location"), item.get("url"),
            item.get("description"), item.get("pub_date"),
            remote=True, restrictions=[item.get("location")],
        ))
    return out


def parse_remoteok():
    data = fetch_json("https://remoteok.com/api")
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        out.append(job(
            "Remote OK", item.get("position"), item.get("company"),
            item.get("location") or "Remote",
            item.get("url") or ("https://remoteok.com/remote-jobs/" + item.get("slug", "")),
            item.get("description"), item.get("date"),
            remote=True, restrictions=[item.get("location")],
        ))
    return out


def parse_weworkremotely():
    """RSS. Each <item> carries a <region> such as 'Anywhere in the World'."""
    raw = fetch("https://weworkremotely.com/categories/remote-programming-jobs.rss")
    root = ET.fromstring(raw)
    out = []
    for item in root.iter("item"):
        title_el = item.find("title")
        region_el = item.find("region")
        link_el = item.find("link")
        desc_el = item.find("description")
        title = clean_text(title_el.text if title_el is not None else "")
        region = clean_text(region_el.text if region_el is not None else "Remote")
        # WWR titles are "Company: Job title"
        company, _, role = title.partition(":")
        if role:
            title, company = role.strip(), company.strip()
        else:
            company = "Not listed"
        out.append(job(
            "We Work Remotely", title, company, region,
            link_el.text if link_el is not None else "",
            desc_el.text if desc_el is not None else "",
            remote=True, restrictions=[region],
        ))
    return out


def parse_arbeitnow():
    """Germany-heavy board. Includes a `remote` flag per posting."""
    out = []
    for page in (1, 2):
        try:
            data = fetch_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        except Exception:
            break
        for item in data.get("data", []):
            out.append(job(
                "Arbeitnow", item.get("title"), item.get("company_name"),
                item.get("location"), item.get("url"),
                item.get("description"), item.get("created_at"),
                remote=bool(item.get("remote")),
                restrictions=[item.get("location")],
                tags=item.get("tags") or [],
            ))
    return out


def parse_landingjobs():
    """Europe-focused. `relocation_paid` is a real sponsorship signal."""
    out = []
    for page in (1, 2, 3):
        try:
            data = fetch_json(f"https://landing.jobs/api/v1/jobs?limit=100&page={page}")
        except Exception:
            break
        if not isinstance(data, list):
            break
        for item in data:
            locations = ", ".join(
                f"{loc.get('city','')} {loc.get('country_code','')}".strip()
                for loc in item.get("locations", []) or []
            )
            out.append(job(
                "Landing.jobs", item.get("title"), item.get("company"),
                locations or "Europe", item.get("url"),
                (item.get("role_description") or "") + " " + (item.get("main_requirements") or ""),
                item.get("published_at"),
                remote=bool(item.get("remote")),
                restrictions=[locations],
                relocation=bool(item.get("relocation_paid")),
            ))
    return out


class StaffLinkParser(HTMLParser):
    """Pull job links off the public Staff.am quality-assurance category page."""

    def __init__(self):
        super().__init__()
        self.current_href = None
        self.depth = 0
        self.parts = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        if tag == "a" and href.startswith("/en/jobs/quality-assurance/"):
            self.current_href = href
            self.depth = 1
            self.parts = []
        elif self.current_href:
            self.depth += 1

    def handle_data(self, data):
        if self.current_href:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.current_href:
            self.depth -= 1
            if self.depth <= 0:
                title = clean_text(" ".join(self.parts))
                if title:
                    self.items.append((self.current_href, title))
                self.current_href = None
                self.parts = []


def parse_staff_am():
    """Armenia, on-site. Kept as its own tier because it needs no relocation."""
    raw = fetch("https://staff.am/en/jobs/quality-assurance").decode("utf-8", "replace")
    parser = StaffLinkParser()
    parser.feed(raw)
    out = []
    seen = set()
    for href, title in parser.items:
        if href in seen:
            continue
        seen.add(href)
        company = "Armenia employer"
        try:
            detail = fetch("https://staff.am" + href).decode("utf-8", "replace")
            remote_match = re.search(r'"is_remote":(true|false)', detail)
            is_remote = bool(remote_match and remote_match.group(1) == "true")
            description = clean_text(detail)[:3000]
            # The page title is "Company | Job title (code)"
            title_match = re.search(r"<title>([^<]{3,200})</title>", detail, re.I)
            if title_match:
                page_title = clean_text(title_match.group(1))
                if "|" in page_title:
                    candidate = page_title.split("|")[0].strip()
                    if candidate and candidate.lower() != "staff.am":
                        company = candidate
        except Exception:
            is_remote = False
            description = ""
        out.append(job(
            "Staff.am", title, company,
            "Yerevan, Armenia (remote)" if is_remote else "Yerevan, Armenia (on-site)",
            "https://staff.am" + href, description,
            remote=is_remote, restrictions=["armenia"],
        ))
    return out


def fetch_linkedin(url: str, attempts: int = 3) -> str:
    """Fetches one LinkedIn search page, waiting out a 429 rather than giving up."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fetch(url).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                wait = LINKEDIN_DELAY * (attempt * 3)
                print(f"  LinkedIn 429, waiting {wait:.0f}s (attempt {attempt}/{attempts})")
                time.sleep(wait)
                continue
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(LINKEDIN_DELAY)
    raise last_error


# Countries that hire contractors and remote workers from outside most often.
# They are searched before the larger Western markets, which usually want local
# residency.
CONTRACTOR_COUNTRIES = {
    "Poland", "Portugal", "Romania", "Bulgaria", "Estonia", "Lithuania", "Latvia",
    "Czechia", "Slovakia", "Hungary", "Croatia", "Serbia", "Greece", "Georgia",
    "Moldova", "Cyprus", "Malta",
}


def _normalise_name(text: str) -> str:
    """'SoftSwiss' and 'SOFTSWISS' and 'Soft Swiss' all compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def parse_linkedin():
    """LinkedIn's public guest endpoint. No login, no API key, no cookies.

    This turned out to be the single best source for Armenia. Staff.am carried five
    Yerevan QA roles; the same search here returns seventy-five unique postings,
    including employers that never list on Staff.am (Tabby, The Vault, Insight,
    Siemens EDA, Broadridge, ServiceTitan).

    The endpoint returns a fragment of job cards, ten at a time. It rate limits
    politely, so the numbers below are deliberately modest.

    One trap: searching location="Armenia" resolves to Armenia, Colombia. The
    location string has to be "Yerevan, Armenia".
    """
    # Armenia first, because that is where nothing has to happen before he can start.
    # Then remote roles in the European countries that actually hire contractors and
    # remote workers from outside: Poland, Portugal, Romania, Bulgaria, the Baltics.
    # Those postings name a country, so they land in tier 3 with a warning, but they
    # are real opportunities and there are a hundred of them on a good day.
    armenia_searches = [
        ("QA", "Yerevan, Armenia"),
        ("QA Automation", "Yerevan, Armenia"),
        ("Test Engineer", "Yerevan, Armenia"),
    ]

    # Every country the radar knows how to recognise is also a country it
    # searches. The order is deliberate: contractor-friendly markets first, then
    # the large Western ones, then the smaller markets at the end. LinkedIn
    # throttles long runs, so if a burst of 429s cuts the plan short it cuts the
    # tail, and the countries most likely to hire from Yerevan have already been
    # fetched.

    out = []
    seen = set()

    # The plan is ordered by value, because LinkedIn starts answering 429 partway
    # through a long run and a trimmed plan should lose the tail, not the head:
    #
    #   1 Armenia on site            9 queries, the only roles needing no paperwork
    #   2 employers by name          one query each, verified afterwards
    #   3 contractor-friendly countries
    #   4 regional searches          European Union and EMEA
    #   5 sponsorship queries
    #   6 the remaining countries
    # Armenia is searched on every run, because it needs no paperwork and a role
    # there is worth more than any other. The remaining 91 queries are split into
    # shards and one shard is taken per run, chosen by the two-hour slot.
    #
    # Why not everything every time. At twelve runs a day, the full plan would be
    # twelve hundred LinkedIn queries daily against an endpoint that starts
    # answering 429 after about a dozen in a row. The run would spend most of its
    # twenty minute budget waiting for throttles and often fail outright. Rotating
    # keeps each run to roughly forty queries, and every shard still comes round
    # twice a day.
    #
    # The cheap sources are unaffected: Telegram, the inbox, and the ten other
    # feeds are read in full on every run, so a genuinely new posting is caught
    # within two hours regardless of which LinkedIn shard is up.
    armenia_plan = [(k, l, st, False, None)
                    for k, l in armenia_searches for st in (0, 10, 25)]

    shards = linkedin_shards()
    if os.getenv("LINKEDIN_ROTATE", "1") == "1":
        slot = linkedin_slot()
        shard_name, shard = shards[slot % len(shards)]
        print(f"  LinkedIn rotation: slot {slot:02d}, this run covers {shard_name} "
              f"({len(shard)} queries)")
        plan = armenia_plan + shard
    else:
        plan = armenia_plan + [item for _, shard in shards for item in shard]

    budget = int(os.getenv("LINKEDIN_MAX_QUERIES", "60"))
    if len(plan) > budget:
        print(f"  LinkedIn plan trimmed from {len(plan)} to {budget} searches")
        plan = plan[:budget]

    for keywords, location, start, remote_only, require_company in plan:
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               f"keywords={urllib.parse.quote(keywords)}")
        if location:
            url += f"&location={urllib.parse.quote(location)}"
        url += f"&start={start}"
        if remote_only:
            url += "&f_WT=2"
        try:
            raw = fetch_linkedin(url)
        except Exception as exc:
            print(f"  LinkedIn search skipped ({location or keywords}): {type(exc).__name__}")
            time.sleep(LINKEDIN_DELAY)
            continue
        time.sleep(LINKEDIN_DELAY)
        cards = raw.split('data-entity-urn="urn:li:jobPosting:')
        if len(cards) < 2:
            continue
        for card in cards[1:]:
                title_m = re.search(r'base-search-card__title">\s*(.*?)\s*</h3>', card, re.S)
                comp_m = re.search(r'base-search-card__subtitle">\s*<a[^>]*>\s*(.*?)\s*</a>', card, re.S)
                loc_m = re.search(r'job-search-card__location">\s*(.*?)\s*</span>', card, re.S)
                link_m = re.search(r'href="(https://[a-z]{2}\.linkedin\.com/jobs/view/[^"]+)"', card)
                date_m = re.search(r'datetime="([^"]+)"', card)

                if not link_m:
                    continue
                link = html.unescape(link_m.group(1)).split("?")[0]
                if link in seen:
                    continue
                seen.add(link)

                def grab(m):
                    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""

                location_text = grab(loc_m)

                # LinkedIn ignored the company name, so drop the cards that are not
                # that employer. Without this a search for SoftSwiss adds a QA job
                # at Wawa Inc in Pennsylvania.
                if require_company:
                    found_company = grab(comp_m)
                    if _normalise_name(require_company) not in _normalise_name(found_company):
                        continue
                # The f_WT=2 filter guarantees the posting is remote even when the
                # location field only names the city, which is what LinkedIn does
                # for "remote, based in Poland" style ads.
                is_remote = remote_only or "remote" in location_text.lower()
                out.append(job(
                    "LinkedIn", grab(title_m), grab(comp_m), location_text, link,
                    "", date_m.group(1) if date_m else "",
                    remote=is_remote,
                    restrictions=[location_text] if location_text else [],
                ))
    return out


# Feeds that publish schema.org-ish RSS. One entry per board, picked because the
# country it covers has no other source in the list: Germany through Berlin
# Startup Jobs, which is the tech board most Berlin startups actually use.
RSS_SOURCES = [
    ("Berlin Startup Jobs", "https://berlinstartupjobs.com/engineering/feed/"),
]


def parse_rss():
    """Generic RSS reader, so adding a country feed is one line, not one parser."""
    out = []
    for name, feed_url in RSS_SOURCES:
        try:
            raw = fetch(feed_url).decode("utf-8", "replace")
        except Exception as exc:
            print(f"  {name}: skipped ({type(exc).__name__})")
            continue
        for item in re.findall(r"<item>(.*?)</item>", raw, re.S)[:60]:
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
            link = re.search(r"<link>(.*?)</link>", item, re.S)
            body = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item, re.S)
            date = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
            if not link:
                continue
            out.append(job(
                name,
                html.unescape(title.group(1)).strip() if title else "",
                "",
                "",
                html.unescape(link.group(1)).strip(),
                html.unescape(body.group(1)) if body else "",
                date.group(1).strip() if date else "",
                remote="remote" in (title.group(1) if title else "").lower(),
            ))
    return out


def parse_devbg():
    """dev.bg, the board Bulgarian developers actually use.

    Structure, checked against the live page: each posting is
    <div class="job-list-item ..." data-job-id="N"> holding
    <span class="company-name">, <h6 class="job-title"> and the apply link.
    """
    out = []
    raw = fetch("https://dev.bg/company/jobs/?s=qa").decode("utf-8", "replace")
    for block in raw.split('class="job-list-item')[1:]:
        block = block[:2500]
        link = re.search(r'href="(https://dev\.bg/company/jobads/[^"]+)"', block)
        title = re.search(r'class="job-title[^"]*"[^>]*>(.*?)</h6>', block, re.S)
        company = re.search(r'class="company-name[^"]*"[^>]*>(.*?)</span>', block, re.S)
        if not link or not title:
            continue
        out.append(job(
            "dev.bg",
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title.group(1))).strip(),
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", company.group(1))).strip() if company else "",
            "Bulgaria",
            html.unescape(link.group(1)),
            "",
            "",
            remote="remote" in block.lower(),
        ))
    return out


def parse_cvbankas():
    """cvbankas.lt, the largest Lithuanian board.

    Each posting is <article ... id="job_ad_N"> with <h3 class="list_h3"> for the
    title, the company in <span class="dib mt5 mr5"> and the city in
    <span class="list_city">.
    """
    out = []
    raw = fetch("https://www.cvbankas.lt/?keyw=qa").decode("utf-8", "replace")
    for block in raw.split('id="job_ad_')[1:]:
        block = block[:2500]
        link = re.search(r'href="(https://www\.cvbankas\.lt/[^"]+)"', block)
        title = re.search(r'class="list_h3"[^>]*>(.*?)</h3>', block, re.S)
        company = re.search(r'class="dib mt5 mr5"[^>]*>(.*?)</span>', block, re.S)
        city = re.search(r'class="list_city"[^>]*>(.*?)</span>', block, re.S)
        if not link or not title:
            continue
        out.append(job(
            "cvbankas.lt",
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title.group(1))).strip(),
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", company.group(1))).strip() if company else "",
            "Lithuania" + (", " + city.group(1).strip() if city else ""),
            html.unescape(link.group(1)),
            "",
            "",
            remote=False,
        ))
    return out


def parse_djinni():
    """Djinni publishes schema.org JobPosting JSON-LD. Eastern Europe, often remote."""
    out = []
    seen_urls = set()
    for keyword in ("QA", "QA Automation", "Manual QA", "Test Automation"):
        try:
            url = ("https://djinni.co/jobs/?primary_keyword="
                   + urllib.parse.quote(keyword) + "&page=1")
            raw = fetch(url).decode("utf-8", "replace")
        except Exception:
            continue
        for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                                raw, re.S):
            if "JobPosting" not in block:
                continue
            try:
                data = json.loads(block.strip())
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                link = item.get("url", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                req = item.get("applicantLocationRequirements") or {}
                country = ""
                if isinstance(req, dict):
                    country = (req.get("address") or {}).get("addressCountry", "")
                elif isinstance(req, list):
                    country = " ".join(
                        ((r.get("address") or {}).get("addressCountry", "") if isinstance(r, dict) else "")
                        for r in req
                    )
                is_remote = item.get("jobLocationType") == "TELECOMMUTE"
                org = item.get("hiringOrganization") or {}
                out.append(job(
                    "Djinni", item.get("title"), org.get("name", ""),
                    ("Remote" if is_remote else "On-site") + (f" ({country})" if country else ""),
                    link, strip_tags(item.get("description", "")),
                    item.get("datePosted"),
                    remote=is_remote,
                    restrictions=[country] if country else [],
                ))
    return out


def parse_email():
    """Job alerts from the inbox, when the mailbox credentials exist.

    Returns an empty list when they do not, so the radar keeps working for
    anyone who has not set up an App Password yet.
    """
    if not (os.getenv("EMAIL_USER") and os.getenv("EMAIL_APP_PASSWORD")):
        return []
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from email_alerts import collect
        jobs, applications, _ = collect(days=int(os.getenv("EMAIL_LOOKBACK_DAYS", "7")))
        if applications:
            path = Path("email_applications.json")
            path.write_text(json.dumps(applications, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"  email: {len(jobs)} unique posting(s), {len(applications)} application reply(s)")
        return jobs
    except Exception as exc:
        print(f"  email: skipped ({type(exc).__name__}: {exc})")
        return []


def parse_telegram():
    """QA postings from the public job channels on Telegram.

    No credentials, because it reads the same public web preview a browser would.
    A private channel cannot be read this way; see telegram_channels.py.
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from telegram_channels import configured_channels, parse_telegram_channels
        if not configured_channels():
            return []
        return parse_telegram_channels()
    except Exception as exc:
        print(f"  telegram: skipped ({type(exc).__name__}: {exc})")
        return []


SOURCES = [
    ("LinkedIn", parse_linkedin),
    ("Email alerts", parse_email),
    ("Telegram channels", parse_telegram),
    ("Himalayas", parse_himalayas),
    ("Djinni", parse_djinni),
    ("Remotive", parse_remotive),
    ("Jobicy", parse_jobicy),
    ("Working Nomads", parse_workingnomads),
    ("We Work Remotely", parse_weworkremotely),
    ("Remote OK", parse_remoteok),
    ("Landing.jobs", parse_landingjobs),
    ("Arbeitnow", parse_arbeitnow),
    ("Staff.am", parse_staff_am),
    ("Berlin Startup Jobs", parse_rss),
    ("dev.bg", parse_devbg),
    ("cvbankas.lt", parse_cvbankas),
]


# --------------------------------------------------------------------------- #
# Filtering and scoring
# --------------------------------------------------------------------------- #

def haystack(job_):
    return f"{job_['title']} {job_['description']} {job_['location']}".lower()


def tidy_title(title: str) -> str:
    """Removes a source's own job number from the front of a title.

    Djinni publishes "1214 | Senior Agentic QA Engineer", and that number reached
    the Telegram message and the sheet. It means nothing to a human.
    """
    cleaned = re.sub(r"^\s*\d{2,6}\s*[|\-:]\s*", "", title or "")
    return re.sub(r"\s+", " ", cleaned).strip() or (title or "").strip()


def title_says_software_qa(job_) -> bool:
    title = job_["title"].lower()
    if any(bad in title for bad in NOT_A_QA_TITLE):
        return False
    return any(re.search(p, title) for p in SOFTWARE_QA_TITLE_PATTERNS)


def is_scam(job_) -> bool:
    text = haystack(job_)
    if any(name in text for name in SCAM_BLOCKLIST):
        return True
    return any(phrase in text for phrase in SCAM_PHRASES)


# Short industry terms that must match as whole words. Without the word
# boundaries, "ems " matched inside "systems ", so every employer with Systems in
# its name was silently thrown away: "EPAM Systems LLC" and "agentic RAG systems"
# both read as electronics manufacturing. "capa" matched inside "capabilities",
# "spc " inside "spec ", and "8d " inside a hash.
SHORT_TERMS = {
    "ems", "odm", "npi", "spc", "msa", "8d", "gmp", "fda", "smt", "emc", "iec",
    "capa", "capa.", "cpk", "grr", "gr&r", "anü", "pcb", "haccp", "sop", "oos",
    "iq", "oq", "pq", "capa,", "qms",
}

_WORD_TERM = re.compile(
    r"\b(" + "|".join(sorted({re.escape(t.strip()) for t in SHORT_TERMS},
                              key=len, reverse=True)) + r")\b"
)


# Boards tag a posting with its field, and that tag is more reliable than the
# title. "AI Automation Engineer" at United Media is filed under Marketing and is
# about building an investor audience, not testing software. The title passed the
# QA patterns; the tag does not lie.
NON_QA_FIELDS = [
    "marketing", "online marketing", "public relations", "corporate communication",
    "sales", "business development", "personnel", "human resources", "recruiting",
    "customer service", "content", "seo", "social media", "brand", "finance",
    "accounting", "legal", "administration", "product management", "design",
    "graphic", "copywriting", "translation", "teaching", "nursing", "logistics",
    "warehouse", "driver", "retail", "hospitality", "call center",
]

QA_FIELD_HINTS = ["qa", "quality", "test", "automation", "sdet", "engineering"]


def tagged_out_of_field(job_) -> bool:
    """True when the board's own category says this is not a testing job."""
    tags = job_.get("tags") or []
    if not tags:
        return False
    joined = " ".join(str(t).lower() for t in tags)
    if any(hint in joined for hint in QA_FIELD_HINTS):
        return False
    return any(field in joined for field in NON_QA_FIELDS)


def is_hard_excluded(job_) -> bool:
    text = haystack(job_)
    # Long, unambiguous phrases are still plain substrings.
    for term in HARD_EXCLUDE:
        if term.strip().lower() in SHORT_TERMS:
            continue
        if term in text:
            return True
    # Short ones have to stand alone as words.
    return bool(_WORD_TERM.search(text))


def needs_missing_language(job_) -> bool:
    text = haystack(job_)
    if any(term in text for term in LANGUAGE_BLOCKERS):
        return True
    title = job_["title"].lower()
    return any(hint in title for hint in FOREIGN_TITLE_HINTS)


DJINNI_BAD_NATIVE = [
    "ukrainian", "russian", "polish", "kazakh", "belarusian",
]


def djinni_detail_blocks(job_) -> bool:
    """Fetches a Djinni detail page and reads the two sections the API omits.

    Djinni publishes a clean JSON-LD block, but the language requirement and the
    candidate-country rule live only in the rendered page. Four Ukrainian-only
    postings reached the chat before this check existed, so it is worth the extra
    request for the handful of jobs that survive the other filters.
    """
    try:
        page = fetch(job_["url"]).decode("utf-8", "replace")
    except Exception:
        return False  # network trouble should not silently drop a good job

    # Required languages block: "<name>English</name> ... <value>B1</value>"
    lang_block = ""
    marker = "Required languages"
    if marker in page:
        start = page.index(marker)
        lang_block = clean_text(page[start:start + 2500]).lower()

    # Djinni renders "English  B2 - Upper Intermediate" as name/value rows, so a
    # C1 or C2 in that block means a level he does not have.
    if "native" in lang_block:
        for language in DJINNI_BAD_NATIVE:
            if language in lang_block:
                return True
    for bad_level in ("c1 - advanced", "c2 - proficient"):
        if bad_level in lang_block and "english" in lang_block:
            return True

    # Candidate country rule
    text = clean_text(page).lower()
    if "countries we consider candidates" in text:
        idx = text.index("countries we consider candidates")
        window = text[idx:idx + 220]
        if "armenia" not in window:
            return True

    if any(phrase in text for phrase in (
        "only consider candidates with experience",
        "only consider candidates located",
        "ukraine-based role",
    )):
        if "armenia" not in text:
            return True

    return False


def locked_to_other_country(job_) -> bool:
    """True when the posting names one country and that country is not Armenia.

    This is the check that was missing. Djinni postings say "Ukraine" in a separate
    field rather than in the body text, so four Ukrainian-only roles were reaching
    the chat with a 97% fit score. A posting that will not hire outside Armenia's
    own country is worthless no matter how well the skills match.
    """
    text = haystack(job_)
    if "armenia" in text or "yerevan" in text:
        return False
    return any(marker in text for marker in COUNTRY_LOCK_MARKERS)


# --------------------------------------------------------------------------- #
# Geography self-test
#
# Run with:  python3 src/job_radar.py --selftest-geo
# Every case below is a real posting that behaved wrongly at some point, or a
# wording Ahmad has been rejected by. A change that breaks one of these should be
# caught before it reaches GitHub Actions.
# --------------------------------------------------------------------------- #

GEO_SELFTEST = [
    # (location, want, why it is in the list)
    ("Remote worldwide",                        True,     "the plain good case"),
    ("Fully remote worldwide",                  True,     ""),
    ("Remote",                                  True,     "no country attached"),
    ("Remote, Europe",                          True,     "region, no single country"),
    ("Remote, EMEA",                            True,     ""),
    ("Remote - Europe",                         True,     ""),
    # Armenia is Tier 1 via is_armenia() and never reaches remote_is_open, so it
    # is checked separately below rather than in this table.

    ("Remote - Countries of Europe or Ukraine", False,
     "RALABS and CORE WILL SOFT both rejected on this exact line"),
    ("Remote (UA)",                             False,    "Djinni country tag"),
    ("EU only",                                 False,    "Armenia is not in the EU"),
    ("Remote (EU citizens only)",               False,    ""),
    ("Remote - EU work permit required",        False,    ""),
    ("Remote (USA only)",                       False,    ""),
    ("Bangalore, India",                        False,    "Fidelity"),
    ("Remote, India",                           False,    ""),
    # These resolve to "single", which Tier 3 renders as one country named. They
    # must not be read as a friendly open region.
    ("Remote (Poland)",                         "single", ""),
    ("Remote, Poland only",                     "single", ""),
    ("Remote (Hungary)",                        "single", "HexaIO"),
    ("Budapest, Hungary",                       "single", ""),
    ("Berlin, Germany",                         "single", ""),
]


def _geo_selftest() -> int:
    """Print the geography table and return the number of failures."""
    failures = 0
    print(f"{'location':45} {'want':9} {'got':10} result")
    print("-" * 82)
    for location, want, why in GEO_SELFTEST:
        job_ = {
            "title": "QA Engineer",
            "location": location,
            "remote": "remote" in location.lower(),
            "description": "",
            "restrictions": [],
            "company": "X",
            "url": "",
        }
        got = remote_is_open(job_) or "-"
        if want is True:
            ok = got != "-"
        elif want is False:
            ok = got == "-"
        else:
            ok = got == want
        if not ok:
            failures += 1
        note = "" if ok else f"   <- {why or 'regression'}"
        print(f"{location:45} {str(want):9} {got:10} {'OK' if ok else 'FAIL'}{note}")
    # Armenia is handled by is_armenia() and gets Tier 1, bypassing
    # remote_is_open entirely. Check that separately.
    for home in ("Yerevan, Armenia", "Remote, Armenia", "Armenia"):
        job_ = {"title": "QA Engineer", "location": home, "remote": "remote" in home.lower(),
                "description": "", "restrictions": [], "company": "X", "url": ""}
        ok = is_armenia(job_)
        if not ok:
            failures += 1
        print(f"{home:45} {'True':9} {'tier 1':10} {'OK' if ok else 'FAIL'}")

    print()
    if failures:
        print(f"{failures} FAILED")
    else:
        print(f"all {len(GEO_SELFTEST) + 3} geography checks passed")
    return failures


def location_text(job_) -> str:
    """Everything the posting says about where it hires, lowercased."""
    return " ".join(job_["restrictions"] + [job_["location"]]).lower()


def is_armenia(job_) -> bool:
    return any(m in location_text(job_) for m in ARMENIA_MARKERS)


# Wording that means Germany, including the German-language forms that a
# deutschsprachige Anzeige uses in the location field.
GERMANY_MARKERS = [
    "germany", "deutschland", "bundesrepublik", "brd", "dach region",
    "berlin", "munich", "münchen", "muenchen", "hamburg", "frankfurt",
    "cologne", "köln", "koeln", "stuttgart", "düsseldorf", "duesseldorf",
    "dusseldorf", "leipzig", "dresden", "nuremberg", "nürnberg", "nurnberg",
    "hanover", "hannover", "bremen", "bonn", "karlsruhe", "mannheim",
    "potsdam", "jena", "darmstadt", "essen", "dortmund", "bielefeld",
]


def is_germany(job_) -> bool:
    """True when the posting is in Germany, on-site or remote.

    Deliberately separate from EUROPE_COUNTRIES. Germany is the one country he
    named for on-site work as well as remote, so it is ranked on its own and
    should not depend on the sponsorship wording that the other on-site
    countries need.

    A posting that says "Remote, Europe" is not German. A posting that says
    "Berlin, Germany" or "Munich" or "Remote, Deutschland" is.
    """
    text = location_text(job_)
    return any(m in text for m in GERMANY_MARKERS)


def named_countries(job_) -> list:
    """Which countries the posting actually names, as opposed to vague wording."""
    text = location_text(job_)
    found = [c for c in EUROPE_COUNTRIES if c in text]
    # Two-letter codes are only trustworthy when they stand alone
    for token in re.findall(r"\b([a-z]{2})\b", text):
        if token in COUNTRY_CODES and token not in found:
            found.append(token)
    return found


REMOTE_PLAIN = ["remote", "fully remote", "remote-first", "remote first", "100% remote"]

# Countries that are never reachable from Yerevan without a visa, so naming one of
# them means the role is closed even if the word "remote" appears somewhere.
BLOCKED_SINGLE_COUNTRIES = [
    "united states", "usa", "u.s.", "us only", "canada", "mexico", "brazil",
    "argentina", "india", "pakistan", "bangladesh", "nigeria", "kenya",
    "south africa", "singapore", "japan", "china", "south korea", "australia",
    "new zealand", "philippines", "indonesia", "malaysia", "vietnam", "thailand",
    "ukraine", "russia", "belarus", "kazakhstan", "uzbekistan", "egypt",
    "saudi arabia", "uae", "israel", "qatar", "kuwait", "morocco", "tunisia",
]

# Two-letter codes for the same closed countries, used inside brackets such as
# "Remote (UA)" by Djinni and "Lisbon PT" by Landing.jobs.
BLOCKED_COUNTRY_CODES = {"ua", "ru", "by", "kz", "uz", "us", "ca", "in", "br",
                         "mx", "ar", "ng", "ke", "za", "sg", "jp", "cn", "kr",
                         "au", "nz", "ph", "id", "my", "vn", "th", "il", "ae",
                         "sa", "qa", "kw", "ma", "tn", "eg"}


# Wording that explicitly EXCLUDES Armenia. Ahmad was rejected by Ralabs and
# CoreWillSoft on exactly this line: "Countries of Europe or Ukraine". Armenia is
# in neither group, so the role was never open to him however good the CV was.
# These are checked before anything else, because "europe" on its own would
# otherwise be read as a friendly region.
EXCLUDES_ARMENIA = [
    "countries of europe or ukraine",
    "europe or ukraine",
    "europe and ukraine only",
    "eu or ukraine",
    "eu/ukraine",
    "europe, ukraine",
    "ukraine or europe",
    "europe without armenia",
    "eu only", "eu-only", "eu only.",
    "europe only", "europe-only",
    "european union only",
    "only eu", "only europe",
    "must be based in the eu", "must reside in the eu", "must be located in the eu",
    "eu residency", "eu citizens only", "eu citizenship",
    "eu work permit", "eu work authorization", "right to work in the eu",
    "european work permit", "european passport",
    "eea only", "eea citizens",
    "schengen only",
    "within the eu", "reside in europe",
]


def excludes_armenia(job_) -> bool:
    """True when the posting names a group Armenia is not in."""
    text = location_text(job_)
    return any(marker in text for marker in EXCLUDES_ARMENIA)


def remote_is_open(job_) -> str:
    """How open is this remote role? Returns 'worldwide', 'region', 'single' or ''.

    This replaces the old check, which looked for the word "remote" and therefore
    matched everything, including "Remote (UA)". Wording matters more than the
    presence of the word remote: "remote worldwide" and "remote, Poland only" are
    not the same job.

    Order matters. An explicit Armenia exclusion is checked first, then a named
    country, and only then the vague regional wording. The previous order let
    "Remote - Countries of Europe or Ukraine" through as a friendly region,
    because it never checked what the region actually was.
    """
    text = location_text(job_)

    # Nothing else matters if the posting says Armenia is out.
    if excludes_armenia(job_):
        return ""

    # A named country decides, before any regional wording gets a say. This is
    # what stops "Remote (Poland)" and "Berlin, Germany" being read as open.
    countries = named_countries(job_)
    if len(countries) > 1:
        return "region"
    if len(countries) == 1:
        single = countries[0]
        if single in EUROPE_COUNTRIES:
            return "single"
        return ""

    if any(m in text for m in REMOTE_WORLDWIDE):
        return "worldwide"
    if any(m in text for m in REMOTE_REGION):
        return "region"
    if len(countries) > 1:
        return "region"
    if len(countries) == 1:
        return "single"
    # A named country that Ahmad cannot legally work in closes the role, whatever
    # the word "remote" is doing in the same field.
    if any(c in text for c in BLOCKED_SINGLE_COUNTRIES):
        return ""
    # Same check for two-letter codes, but only in brackets or after a comma so
    # that the "us" inside "business" does not trigger it
    for code in re.findall(r"[(,\s]([a-z]{2})[)\s,]", text):
        if code in BLOCKED_COUNTRY_CODES:
            return ""

    # "Remote" on its own, with no country attached, is as open as it gets
    if any(m in text for m in REMOTE_PLAIN) or not text.strip():
        if job_["remote"]:
            return "worldwide"
    return ""


def wants_sponsorship(job_) -> bool:
    return job_["relocation"] or any(
        marker in haystack(job_) for marker in SPONSORSHIP_MARKERS
    )


def assign_tier(job_):
    """Returns 1, 2, 3, 4 or None. Lower is better.

    1   Armenia, on-site or remote
    2   Remote and genuinely open, worldwide or regional
    25  Germany, on-site or remote. He asked for Germany by name, both ways
    3   Remote but one European country named
    4   On-site in Europe with visa sponsorship or relocation offered
    """
    # Tier 1: Armenia, no relocation, no visa, start immediately
    if is_armenia(job_):
        return 1

    if job_["remote"]:
        openness = remote_is_open(job_)
        if openness in ("worldwide", "region"):
            # "Remote, Europe" is not German. Only wording that actually names
            # Germany gets the Germany row; everything else open stays tier 2,
            # because working from Yerevan beats moving to Berlin.
            return 25 if is_germany(job_) else 2
        if openness == "single":
            # A remote role that names Germany usually means "you must be in
            # Germany", which is the case he asked for, so it keeps the Germany
            # row instead of dropping to the generic single-country one.
            if is_germany(job_):
                return 25
            # Any other European country named. Readable, but flag it.
            if set(named_countries(job_)) & set(EUROPE_COUNTRIES):
                return 3
        return None

    # Germany, on-site, no sponsorship language needed. He asked for Germany by
    # name for both on-site and remote work, and the EU Blue Card route makes a
    # German employer the most realistic on-site option he has. It sits at its own
    # row, below open remote and above the single-country remote roles.
    text = location_text(job_)
    if is_germany(job_):
        return 25

    # On-site elsewhere. Only worth a look when they offer to move him.
    if wants_sponsorship(job_):
        in_europe = (any(c in text for c in EUROPE_COUNTRIES)
                     or any(city in text for city in EUROPE_CITIES))
        if in_europe:
            return 4
    return None


# Ahmad has five years, so trainee and junior postings are a step backwards and
# principal or architect postings are a stretch. Middle and senior fit best.
LEVEL_BELOW = ["trainee", "intern", "junior", "entry level", "graduate", "apprentice"]
LEVEL_ABOVE = ["principal", "architect", "head of", "director", "vp ", "staff "]
LEVEL_GOOD = ["middle", "senior", "regular", "strong", "lead", "engineer", "sdet"]


def level_adjustment(job_) -> int:
    title = job_["title"].lower()
    if any(w in title for w in LEVEL_BELOW):
        return -18
    if any(w in title for w in LEVEL_ABOVE):
        return -8
    if any(w in title for w in LEVEL_GOOD):
        return 4
    return 0


def relevance_score(job_) -> int:
    """Skill overlap, 20 to 97.

    Jobs arriving without a description (LinkedIn does not serve one to a guest)
    are scored on the title alone and capped lower, because there is genuinely less
    evidence. Pretending otherwise would put a good Armenian role below a remote
    role purely because of what LinkedIn chose to publish.
    """
    text = haystack(job_)
    matched = [s for s in dict.fromkeys(QA_SKILLS) if s in text]
    score = 40 + len(matched) * 4
    if any(w in text for w in ("playwright", "postman", "api testing", "regression")):
        score += 8
    if any(w in text for w in ("manual", "exploratory", "test case", "jira")):
        score += 6
    if "5+" in text or "4+" in text or "3+ years" in text:
        score += 4
    score += level_adjustment(job_)

    if not job_["description"].strip():
        # Title-only evidence. Base it on the title words we do have.
        title = job_["title"].lower()
        title_hits = [k for k in QA_SKILLS if k in title]
        score = 52 + len(title_hits) * 6 + level_adjustment(job_)
        if any(w in title for w in ("automation", "sdet", "aqa")):
            score += 5
        if any(w in title for w in ("manual", "general", "qa engineer")):
            score += 3

    return max(20, min(score, 97))


def job_age_days(job_):
    """Age of the posting in days, or None when the date cannot be read.

    Sources disagree wildly: LinkedIn gives an ISO timestamp, several feeds give
    "2 days ago", and some give nothing. When it is unreadable the job is treated
    as fresh, because dropping a real role over an unparseable date is worse than
    sending a slightly older one.
    """
    raw = (job_.get("created") or job_.get("published") or "").strip()
    if not raw:
        return None
    text = raw.lower()
    match = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", text)
    if match:
        value, unit = int(match.group(1)), match.group(2)
        factor = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}[unit]
        return value * factor
    for pattern in (r"(\d{4}-\d{2}-\d{2})", r"(\d{2})[./](\d{2})[./](\d{4})"):
        found = re.search(pattern, raw)
        if not found:
            continue
        try:
            if found.lastindex == 1:
                stamp = datetime.strptime(found.group(1), "%Y-%m-%d")
            else:
                stamp = datetime.strptime(f"{found.group(3)}-{found.group(2)}-{found.group(1)}",
                                          "%Y-%m-%d")
            return max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - stamp).days)
        except ValueError:
            continue
    return None


def is_stale(job_) -> bool:
    """Old enough that the posting is probably closed.

    Ten days is the default because a QA posting usually gathers its shortlist in
    the first week or two. Override with JOB_MAX_AGE_DAYS.
    """
    limit = int(os.getenv("JOB_MAX_AGE_DAYS", "10"))
    age = job_age_days(job_)
    return age is not None and age > limit


def has_description(job_) -> bool:
    return bool(job_["description"].strip())


def matched_skills(job_):
    text = haystack(job_)
    return [s for s in dict.fromkeys(QA_SKILLS) if s in text][:8]


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

LINKEDIN_JOB_ID = re.compile(r"/jobs/view/(?:[^/]*-)?(\d{6,})")

# LinkedIn's guest endpoint returns 429 once you make a dozen requests in a burst.
# The delay and the backoff below are what keeps 27 country searches working
# instead of half of them failing silently.
LINKEDIN_DELAY = float(os.getenv("LINKEDIN_DELAY", "1.4"))


def normalise_url(url: str) -> str:
    """Cuts the parts of a URL that change without the job changing.

    The same LinkedIn posting shows up as pt.linkedin.com, www.linkedin.com and
    uk.linkedin.com, with different tracking parameters on each. Treating those as
    three different jobs is why duplicates kept arriving.
    """
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0].rstrip("/").lower()
    match = LINKEDIN_JOB_ID.search(url)
    if match:
        return f"linkedin:{match.group(1)}"
    # Staff.am and similar boards add a numeric suffix per posting; keep it.
    return url


# Sources that do not publish an employer name all fall back to this string.
# Three postings on the live run collapsed to "notlisted|..." keys, which means a
# QA role with no company name was indistinguishable from every other QA role with
# no company name. It is a sentinel, not a company.
UNKNOWN_COMPANY = "notlisted"


def job_key(job_):
    """Identity for the seen list.

    Company plus title, not URL. The same role gets posted on Djinni and on
    LinkedIn and on the company's own board, and the URLs have nothing in common,
    so a URL-based key lets the same job through three times under three names.

    When the source did not publish a company, the title alone is not an identity:
    on the first real run, "Not listed" appeared as the company on three different
    postings and all three shared the sentinel. Those fall back to the URL, which
    is unique per posting, rather than to a key that collides.
    """
    company = re.sub(r"[^a-z0-9]", "", (job_.get("company") or "").lower())
    title = re.sub(r"[^a-z0-9]", "", (job_.get("title") or "").lower())
    if company and company != UNKNOWN_COMPANY and title:
        return f"{company}|{title}"
    url = normalise_url(job_.get("url", ""))
    if url:
        return url
    return f"{company}|{title}" if (company or title) else ""


# --------------------------------------------------------------------------- #
# Remembering what has already been sent
#
# Three layers, because each one on its own let the same posting back in:
#
#   1  seen_jobs.json         every key ever sent. Capped at 3000 and sorted,
#                             so on a long enough run the oldest keys fell off
#                             the end and those postings came round again.
#   2  the Jobs tab           the sheet is the record of what he has already
#                             seen or applied to. The radar never read it, so a
#                             posting already sitting in his sheet came back.
#   3  already-notified.json  the keys as sent, kept separate from the sorted
#                             seen list, so age never deletes them.
#
# The third is also seeded with his existing applications the first time it is
# empty, which is what stops the eighteen Armenia rows he already applied to
# from arriving again.
# --------------------------------------------------------------------------- #

ALREADY_FILE = Path(__file__).with_name("already-notified.json")
SHEET_TAB = "Jobs"

# The value stored for keys that came from the sheet rather than from a send. It
# is deliberately not a date: seen_jobs.json is trimmed by date and this file is
# not, so a key that looks like a date invites the same trimming by mistake.
ALREADY_SEEDED = "seed:sheet"


def normalise_key_part(text) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def load_already():
    """The permanent list of postings already notified. Never trimmed by date."""
    try:
        raw = json.loads(ALREADY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_already(already):
    ALREADY_FILE.write_text(
        json.dumps(already, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def sheet_keys() -> set:
    """Company|title for every row already in the Jobs tab.

    Reads the sheet directly over the Sheets API rather than through
    job_sheet_sync, so a radar run does not depend on the sheet sync module
    importing cleanly. Returns an empty set on any failure: a check that cannot
    reach the sheet must never stop the alerts going out, it should only make
    the duplicate guard weaker for that run.
    """
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    if not spreadsheet_id or "docs.google.com" in spreadsheet_id:
        # The full URL in the secret is what broke every Actions run before. Say
        # so loudly here instead of silently skipping the duplicate check.
        if "docs.google.com" in spreadsheet_id:
            print("SPREADSHEET_ID is a URL, not an ID. Duplicate check against "
                  "the sheet is OFF until it is fixed.")
        return set()
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from job_sheet_sync import access_token, sheets_get
        token = access_token()
        rows = sheets_get(token, spreadsheet_id, f"{SHEET_TAB}!A1:E900")
    except Exception as exc:
        print(f"Sheet duplicate check skipped: {type(exc).__name__}: {exc}")
        return set()

    keys = set()
    for row in rows[1:]:
        if len(row) < 4:
            continue
        company = normalise_key_part(row[2])
        title = normalise_key_part(row[3])
        if company and title:
            keys.add(f"{company}|{title}")
        # Also record each half, so a reworded title from a different board on
        # the same company still matches. Stored with a prefix so a company name
        # can never collide with a company|title pair.
        if company:
            keys.add(f"company:{company}")
    return keys


def load_seen():
    """Returns a dict of key -> first-seen ISO date."""
    try:
        raw = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        return raw
    # Old format was a plain list of keys.
    today = datetime.now(timezone.utc).date().isoformat()
    return {k: today for k in raw}


def save_seen(seen):
    """Keeps the newest 3000 entries by date, so old ones age out naturally."""
    ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:3000]
    STATE_FILE.write_text(json.dumps(dict(ordered), ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #

def telegram_send(text, parse_mode="HTML", preview=False):
    """Sends one message.

    parse_mode HTML is the default now, because bold titles, a fit bar and a
    tappable link are the difference between a wall of text and something that
    can be read on a phone in two seconds. Telegram silently drops messages with
    broken markup, so every value that comes from a job posting is escaped before
    it is inserted by format_job.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "false" if preview else "true",
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result)


def telegram_send_document(pdf_path, caption=""):
    """Sends a PDF. Separate from sendMessage because the endpoint and the
    multipart body are different, and because a failed document must never stop
    the text alert from going out.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not CHAT_ID:
        raise RuntimeError("Telegram credentials are not set")

    boundary = "----QAJobRadarBoundary"
    path = Path(pdf_path)
    data = path.read_bytes()
    filename = path.name[:60]

    parts = []
    def field(name, value):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    field("chat_id", CHAT_ID)
    if caption:
        field("caption", caption[:900])
    parts.append(
        (f"--{boundary}\r\n"
         f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
         f"Content-Type: application/pdf\r\n\r\n").encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())

    body = b"".join(parts)
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result)


TIER_BADGE = {
    1: "🇦🇲 Armenia · no paperwork",
    2: "🌍 Remote · open",
    25: "🇩🇪 Germany · your priority",
    3: "🌍 Remote · one country named",
    4: "🛂 On-site Europe",
}

TIER_NOTE = {
    1: "",
    2: "",
    25: "Germany, the country you asked for. Both on-site and remote land here, "
        "and the EU Blue Card is the route that actually exists for a QA "
        "engineer with five years.",
    3: "The country is named. Worth one question about a contractor contract "
       "before you spend an evening on it.",
    4: "On-site, so a permit is needed. Confirm they will support one before "
       "applying, because the process takes months.",
}


def tier_note(job_, tier) -> str:
    return TIER_NOTE.get(tier, "")


def format_heartbeat(stats, new_count, sent_count, sources, sheet_line=None):
    """A short status line, so a quiet day is visibly quiet rather than suspicious.

    Without this, "no message" could mean three different things: no new jobs, a
    broken source, or a dead job. This says which.
    """
    lines = ["🔍 <b>QA Job Radar ran</b>", ""]
    lines.append(f"Scanned <b>{stats.get('total', 0)}</b> postings from {len(sources)} sources")
    lines.append(f"Passed filters: {stats.get('kept', 0)}")
    lines.append(f"New since last run: <b>{new_count}</b>")
    if sent_count:
        lines.append(f"Sent: <b>{sent_count}</b>")
    else:
        lines.append("Sent: none, everything new has already been posted")

    # Say plainly when a source returned nothing, because that is the failure that
    # hides best.
    dead = [name for name, count in sources.items() if count == 0]
    if dead:
        lines.append("")
        lines.append("Sources that returned nothing: " + esc(", ".join(dead)))
    if sheet_line:
        lines.append("")
        lines.append(sheet_line)
    return "\n".join(lines)


def esc(text) -> str:
    """HTML escape for Telegram. Anything from a job posting goes through this."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


FIT_WORDS = [(90, "excellent match"), (80, "strong match"), (70, "good match"),
             (55, "worth a look"), (0, "a stretch")]


def fit_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def fit_word(score: int) -> str:
    for floor, word in FIT_WORDS:
        if score >= floor:
            return word
    return "a stretch"


def format_job(job_, tier):
    """One posting, laid out to be read on a phone.

    Shape: what and where, then how well it fits as a bar, then only the flags
    that change a decision, then the link. Everything from the posting is escaped
    because Telegram rejects a message whose HTML does not parse, and a rejected
    message is a lost job.
    """
    score = relevance_score(job_)
    skills = matched_skills(job_)[:3]
    lines = []

    # Headline. The tier badge already says Armenia or remote, so the location
    # line repeats it only when it adds something, usually the city.
    lines.append(f"{TIER_BADGE[tier]}")
    lines.append(f"<b>{esc(job_['title'])}</b>")

    meta = [esc(job_["company"])]
    location = (job_["location"] or "").strip()
    if location and location.lower() not in ("remote", "not specified"):
        meta.append(esc(location))
    lines.append(" · ".join(part for part in meta if part))

    lines.append("")
    bar = fit_bar(score)
    note = "" if has_description(job_) else " (title only)"
    lines.append(f"{bar} <b>{score}%</b> · {fit_word(score)}{note}")
    if skills:
        lines.append("✅ " + " · ".join(esc(s) for s in skills))

    # Only the flags that change a decision.
    flags = []
    if wants_sponsorship(job_):
        flags.append("🛂 sponsorship or relocation mentioned")
    age = job_age_days(job_)
    if age is not None and age <= 3:
        flags.append("🆕 posted " + ("today" if age == 0 else f"{age} day{'s' if age > 1 else ''} ago"))
    if job_["relocation"]:
        flags.append("🚚 relocation offered")
    if flags:
        lines.append("")
        lines.extend(flags)

    lines.append("")
    lines.append(f'👉 <a href="{esc(job_["url"])}">Open the posting</a>')

    note = tier_note(job_, tier)
    if note:
        lines.append(f"<i>{esc(note)}</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

COMPANY_NOISE = [
    "armenia", "yerevan", "llc", "ltd", "gmbh", "inc", "corp", "co",
    "limited", "group", "software", "technologies", "solutions", "sv",
]


def normalise_company(name):
    """'TeamViewer Armenia' and 'TeamViewer' are the same employer to a job seeker."""
    text = name.lower()
    # Drop anything after a pipe, which is usually a local translation
    text = text.split("|")[0]
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    words = [w for w in text.split() if w and w not in COMPANY_NOISE]
    return "".join(words)


TITLE_SYNONYMS = {
    "quality": "qa", "assurance": "", "tester": "", "testing": "",
    "engineer": "", "specialist": "", "analyst": "", "manual": "",
    "automation": "auto", "automated": "auto",
}


def normalise_title(title):
    """Same posting on two boards should collapse to one.

    LinkedIn says "Quality Assurance Engineer", Staff.am says "QA Engineer", and both
    describe the same SoftConstruct role. Strip the words that carry no meaning in a
    QA title so they compare equal, but keep "automation" and "manual" because those
    genuinely separate one job from another at the same company.
    """
    text = title.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    words = []
    for w in text.split():
        if w in ("junior", "middle", "senior", "lead", "principal", "intern",
                 "internship", "sr", "jr", "ii", "iii"):
            continue
        words.append(TITLE_SYNONYMS.get(w, w))
    result = " ".join(w for w in words if w)
    # If every word was noise, fall back to the flattened original so two different
    # postings never collapse just because both normalised to nothing.
    if not result:
        result = re.sub(r"[^a-z0-9]", "", title.lower())
    return result


def dedup_signature(j):
    """Same job posted on Staff.am and LinkedIn should reach the chat once.

    A source that did not publish an employer name cannot be deduplicated by
    company, because every such posting shares the "Not listed" sentinel. Three
    live postings collapsed into one key on the first run. Those fall back to the
    URL, which is per posting, so two different QA roles stay two roles.
    """
    company = normalise_company(j["company"])
    title = normalise_title(j["title"])
    if company.replace(" ", "") in ("notlisted", "notspecified", ""):
        return normalise_url(j.get("url", "")) or f"{company}|{title}"
    return company + "|" + title


def collect():
    all_jobs = []
    source_counts = {}
    for name, parser in SOURCES:
        try:
            found = parser()
            all_jobs.append((name, found))
            source_counts[name] = len(found)
            print(f"{name}: {len(found)} fetched")
        except Exception as exc:
            source_counts[name] = 0
            print(f"{name}: skipped ({exc.__class__.__name__}: {exc})")

    kept = []
    stats = {"total": 0, "not_qa": 0, "scam_blocked": 0, "hard_excluded": 0,
             "language": 0, "country_locked": 0, "djinni_detail_blocked": 0,
             "too_old": 0, "wrong_field": 0, "out_of_scope": 0, "kept": 0}
    for _, jobs in all_jobs:
        for j in jobs:
            stats["total"] += 1
            if not j["url"]:
                continue
            if not title_says_software_qa(j):
                stats["not_qa"] += 1
                continue
            if is_scam(j):
                stats["scam_blocked"] += 1
                continue
            if is_hard_excluded(j):
                stats["hard_excluded"] += 1
                continue
            if tagged_out_of_field(j):
                stats["wrong_field"] += 1
                continue
            if needs_missing_language(j):
                stats["language"] += 1
                continue
            if locked_to_other_country(j):
                stats["country_locked"] += 1
                continue
            # Djinni hides its language and country rules in the rendered page,
            # so this one costs an extra request. Everything else is filtered
            # above for free, which keeps it to a handful of fetches per run.
            if j["source"] == "Djinni" and djinni_detail_blocks(j):
                stats["djinni_detail_blocked"] += 1
                continue

            if is_stale(j):
                stats["too_old"] += 1
                continue

            tier = assign_tier(j)
            if tier is None:
                stats["out_of_scope"] += 1
                continue
            j["tier"] = tier
            j["score"] = relevance_score(j)
            kept.append(j)
            stats["kept"] += 1
    # One posting, one message. Keep the version with the richer description,
    # since that is the one whose fit score means something.
    by_sig = {}
    duplicates = 0
    for j in kept:
        sig = dedup_signature(j)
        existing = by_sig.get(sig)
        if existing is None:
            by_sig[sig] = j
            continue
        duplicates += 1
        if len(j["description"]) > len(existing["description"]):
            by_sig[sig] = j

    # Second pass: same company, and one title contains the other
    # ("QA Engineer" vs "QA Engineer - Manual & Automated").
    final = {}
    for j in by_sig.values():
        company = normalise_company(j["company"])
        title = normalise_title(j["title"])
        merged = False
        for key, existing in list(final.items()):
            if not key.startswith(company + "|"):
                continue
            other = key.split("|", 1)[1]
            if title in other or other in title:
                merged = True
                duplicates += 1
                if len(j["description"]) > len(existing["description"]):
                    final[key] = j
                break
        if not merged:
            final[company + "|" + title] = j

    stats["duplicates_removed"] = duplicates
    kept = list(final.values())
    stats["kept"] = len(kept)
    stats["sources"] = source_counts
    print("Filter stats:", stats)
    return kept, stats


def priority_rank(j):
    """The order he asked for, as a single number. Lower sorts first.

    1    Armenia, on site or remote. Nothing to arrange.
    2    Remote, genuinely open. Contractor work is realistic.
    2.4  Germany, on site. He named this market for on-site work, and the Blue
         Card is the route that actually exists.
    2.5  Europe elsewhere with sponsorship offered.
    2.6  Germany, remote. A remote job that happens to name Germany.
    3    Remote but one country named and no sponsorship. Needs a question
         before any effort is spent.
    4    On site elsewhere in Europe with nothing said about permits.
    """
    tier = j["tier"]
    # Germany keeps its own row whether or not sponsorship is mentioned, so the
    # sponsored 2.5 below must not pull it down to the same rank as the others.
    #
    # Germany gets two ranks, not one, because the two kinds of German posting are
    # not equally reachable:
    #
    #   2.4  Berlin, on site. He asked for this, and the EU Blue Card is a route
    #        that exists. Highest rank available outside Armenia.
    #   2.6  Remote, Germany. Still good, but it is a remote job that happens to
    #        name Germany, not the move he asked about.
    #
    # Both sit above an unsponsored single-country remote role and below a fully
    # open remote one.
    if tier == 25:
        return 2.4 if not j["remote"] else 2.6
    sponsored = wants_sponsorship(j)
    if tier in (2, 3, 4) and sponsored:
        return 2.5
    return float(tier)


def send_priority(j):
    """Tier group first, then fresh before old, then fit score.

    Freshness outranks the score on purpose. A role posted yesterday is still
    gathering applications; a perfect match posted three weeks ago has usually
    already chosen someone.
    """
    age = job_age_days(j)
    fresh = 0 if (age is None or age <= 2) else 1
    return (priority_rank(j), fresh, -j["score"])


def trim_to_caps(jobs):
    """Applies the per-tier caps, then rebuilds the send order.

    Kept in one place so --dry-run shows exactly what --once would send. Before this,
    the dry run printed the raw list and the two disagreed.
    """
    trimmed = []
    for tier_value in TIER_ROWS:
        bucket = sorted([j for j in jobs if j["tier"] == tier_value], key=send_priority)
        trimmed.extend(bucket[:MAX_PER_TIER[tier_value]])
    trimmed.sort(key=send_priority)
    return trimmed


def seed_from_sheet(already, sheet) -> int:
    """Writes every sheet row into the permanent notified list, once.

    This is the step that stops the eighteen Armenia rows he has already
    applied to from arriving again on the next run. It runs only when the
    permanent list is empty, so clearing already-notified.json is also the way
    to deliberately forget everything and start over.

    Returns how many keys were added.
    """
    if already:
        return 0
    added = 0
    today = datetime.now(timezone.utc).date().isoformat()
    for key in sheet:
        if ":" in key and key.startswith("company:"):
            continue
        if key not in already:
            already[key] = ALREADY_SEEDED
            added += 1
    return added


def run(dry_run=False, report=False, dump_json=False):
    seen = load_seen()
    already = load_already()
    sheet = sheet_keys()
    seeded = seed_from_sheet(already, sheet)
    if seeded:
        print(f"Seeded {seeded} posting(s) from the Jobs tab as already seen.")
    jobs, stats = collect()

    if dump_json:
        import_json = json.dumps(
            [{k: v for k, v in j.items() if k not in ("description",)} for j in jobs],
            ensure_ascii=False, indent=1,
        )
        Path("radar_jobs.json").write_text(import_json, encoding="utf-8")
        print(f"Wrote {len(jobs)} jobs to radar_jobs.json")

    if report:
        print(f"\n{len(jobs)} jobs passed the filters\n")
        for j in sorted(jobs, key=send_priority):
            print(f"[tier {j['tier']}] {j['score']}%  {j['title']}  @ {j['company']}  ({j['location']})")

    # --json and --report are inspection modes. They never send.
    if report or dump_json:
        return

    today = datetime.now(timezone.utc).date().isoformat()

    def already_sent(job_) -> bool:
        """True when this posting has reached him before, by any route.

        checked in order of cheapest first: the permanent notified list, then the
        rolling seen list, then the sheet. The sheet carries both an exact
        company|title key and a company-only key, so a role at a company already
        in the sheet under a slightly different title does not come back either.
        """
        key = job_key(job_)
        if key in already:
            return True
        if key in seen:
            return True
        company = normalise_key_part(job_.get("company"))
        if company and company != UNKNOWN_COMPANY and f"company:{company}" in sheet:
            return True
        return False

    before = len(jobs)
    new = [j for j in jobs if not already_sent(j)]
    suppressed = before - len(new)
    if suppressed:
        print(f"Dropped {suppressed} posting(s) already sent or already in the sheet.")
    stats["already_seen"] = suppressed

    # Tier first, then score inside the tier.
    new.sort(key=send_priority)
    for j in new:
        key = job_key(j)
        seen[key] = today
        already[key] = today

    if dry_run:
        to_send = trim_to_caps(new)
        counts = {}
        for j in to_send:
            counts[j["tier"]] = counts.get(j["tier"], 0) + 1
        print(f"\n{len(new)} job(s) matched. {len(to_send)} would be sent:")
        if suppressed:
            print(f"   ({suppressed} dropped as already sent or already in the sheet)")
        for tier_value in TIER_ROWS:
            if counts.get(tier_value):
                print(f"   tier {tier_value}: {counts[tier_value]}")
        print()
        for j in to_send:
            print("=" * 60)
            print(format_job(j, j["tier"]))
            print()
        return

    new = trim_to_caps(new)

    sent = 0
    sent_jobs = []
    tailor_enabled = os.getenv("JOB_RADAR_TAILOR", "1") == "1"
    tailor_dir = Path("/tmp/tailored")
    tailor_error_shown = False

    for j in new[:MAX_PER_RUN]:
        try:
            telegram_send(format_job(j, j["tier"]))
            sent += 1
            sent_jobs.append(j)
            time.sleep(1)
        except Exception as exc:
            print(f"Send failed for {j['title']}: {exc}")
            continue

        # Resume and cover letter, generated for this specific posting.
        if not tailor_enabled:
            continue
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from tailor import tailor as build_documents
            resume_pdf, letter_pdf = build_documents(j, tailor_dir)
            caption = f"📄 {j['title']} · {j['company']}"
            telegram_send_document(resume_pdf, caption)
            time.sleep(0.5)
            telegram_send_document(letter_pdf, caption)
            time.sleep(0.5)
            print(f"  documents sent for {j['company']}")
        except Exception as exc:
            # A failure here must not cost him the job alert, so it is logged once
            # and then the loop carries on.
            if not tailor_error_shown:
                print(f"  document generation unavailable: {type(exc).__name__}: {exc}")
                tailor_error_shown = True
    if len(new) > MAX_PER_RUN:
        try:
            telegram_send(f"ℹ️ {len(new) - MAX_PER_RUN} more matched. They will come through on the next run.")
        except Exception:
            pass
    # Decided before the heartbeat is built, so the message can say whether the
    # sheet actually received these rows.
    keep = os.getenv("JOB_RADAR_HEARTBEAT", "1") == "1"

    # Persist exactly what went to Telegram, so the sheet gets the same list
    # rather than a fresh scan that might differ by a few postings.
    Path("sent_jobs.json").write_text(
        json.dumps([{k: v for k, v in j.items() if k != "description"} for j in sent_jobs],
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    # Sheet sync, in the same run. If it fails, the alerts still went out.
    sheet_line = ""
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_REFRESH_TOKEN"):
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from job_sheet_sync import sync_file
            added, skipped = sync_file("sent_jobs.json")
            print(f"Sheet: {added} added, {skipped} already there")
            sheet_line = f"📊 Sheet updated: {added} new row(s)"
            # The real bottleneck is not finding roles, it is leaving approved
            # ones unsent. Read them back from the sheet he already fills in.
            try:
                from job_sheet_sync import unsent_high_fit
                pending = unsent_high_fit(min_fit=85, limit=3)
                if pending:
                    lines = ["📌 <b>Ready and still unsent:</b>"]
                    for row in pending:
                        lines.append(f"   {fit_bar(row['fit'])} <b>{row['fit']}%</b> "
                                     f"{esc(row['company'])} · {esc(row['title'])}")
                    sheet_line += "\n" + "\n".join(lines)
            except Exception as exc:
                print(f"Unsent check skipped: {type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f"Sheet sync failed: {type(exc).__name__}: {exc}")
            sheet_line = f"⚠️ Sheet NOT updated: {type(exc).__name__}"
    else:
        print("Sheet sync skipped: no Google credentials in this run")
        sheet_line = ("⚠️ Sheet NOT updated: GOOGLE_SERVICE_ACCOUNT_JSON and "
                      "SPREADSHEET_ID are not set in the repo secrets")

    if keep:
        try:
            telegram_send(format_heartbeat(stats, len(new), sent,
                                          stats.get("sources", {}), sheet_line))
        except Exception as exc:
            print(f"Heartbeat failed: {exc}")

    save_seen(seen)
    save_already(already)
    print(f"Sent {sent} of {len(new)} new job(s).")


def report_suppressed():
    """--why: read-only. Says how the duplicate layers are doing."""
    seen = load_seen()
    already = load_already()
    sheet = sheet_keys()
    print(f"seen_jobs.json        {len(seen):5d} keys")
    print(f"already-notified.json {len(already):5d} keys")
    print(f"Jobs tab              {len(sheet):5d} keys "
          f"({sum(1 for k in sheet if k.startswith('company:'))} companies)")
    if not already:
        print("\nalready-notified.json is empty, so the next run will seed it "
              "from the Jobs tab.")
    if os.getenv("SPREADSHEET_ID", "").startswith("http"):
        print("\nSPREADSHEET_ID is a URL. It must be the bare ID.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="scan once and send to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="scan once, print only")
    parser.add_argument("--report", action="store_true", help="print everything that passed, grouped by tier")
    parser.add_argument("--selftest-geo", action="store_true",
                        help="check the geography filter against known cases and exit")
    parser.add_argument("--why", action="store_true",
                        help="show what the duplicate layers remember, then exit")
    parser.add_argument("--json", action="store_true",
                        help="also write radar_jobs.json for job_sheet_sync.py")
    args = parser.parse_args()

    if args.selftest_geo:
        raise SystemExit(_geo_selftest())

    if args.why:
        report_suppressed()
        return

    if args.report or args.json:
        run(report=args.report, dump_json=args.json)
    elif args.dry_run or args.once:
        run(dry_run=args.dry_run)
    else:
        print("Run with --once, --dry-run or --report. Nothing was sent.")
        parser.print_help()


if __name__ == "__main__":
    main()
