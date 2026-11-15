#!/usr/bin/env python3
"""
QA Job Radar — software QA roles for a candidate based in Yerevan, Armenia.

Priority order for what gets sent:

  🥇 Tier 1  Remote, open to Europe / EMEA / worldwide / Armenia
  🥈 Tier 2  Armenia — on-site or remote, any employer
  🥉 Tier 3  On-site in Europe, but only when the posting mentions visa
             sponsorship or paid relocation

Anything outside those three tiers is dropped, including the manufacturing and
hardware quality jobs that dominate the "quality engineer" search results.

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
MAX_PER_TIER = {1: 8, 2: 8, 3: 5, 4: 3}
MAX_PER_RUN = 18


# --------------------------------------------------------------------------- #
# What counts as the job Ahmad actually does
# --------------------------------------------------------------------------- #

# The title has to say one of these. Checking the title and not the whole
# description is deliberate: a warehouse job whose text mentions "test" once
# should not get through.
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

# Short country codes used by APIs such as Landing.jobs and Djinni.
COUNTRY_CODES = {
    "at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr",
    "gr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt", "nl", "no",
    "pl", "pt", "ro", "se", "si", "sk", "gb", "uk", "ge", "md", "rs", "tr",
}

SPONSORSHIP_MARKERS = [
    "visa sponsorship", "sponsorship available", "we sponsor", "sponsor visa",
    "visa sponsored", "will sponsor", "sponsorship provided",
    "work permit", "work visa", "relocation package", "relocation support",
    "relocation bonus", "paid relocation", "relocation assistance", "we relocate",
    "relocation offered", "relocation provided", "visa support", "visa assistance",
    "blue card", "eu blue card", "skilled worker visa", "tier 2",
    "certificate of sponsorship", "right to work support",
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
        remote=None, restrictions=None, relocation=False):
    return {
        "source": source,
        "title": clean_text(title),
        "company": clean_text(company) or "Not listed",
        "location": clean_text(location) or "Not specified",
        "url": html.unescape(str(url or "")).strip(),
        "description": clean_text(description)[:4000],
        "published": clean_text(published),
        "remote": remote,
        "restrictions": [r.lower() for r in as_list(restrictions)],
        "relocation": relocation,
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

    # Remote roles across Europe. The first group are the countries that most
    # often hire contractors and remote workers from outside; the second group
    # are the larger Western markets, which are worth reading even though they
    # usually want local residency.
    remote_europe_searches = [
        # contractor-friendly
        ("QA Engineer", "Poland"),
        ("QA Engineer", "Portugal"),
        ("QA Engineer", "Romania"),
        ("QA Engineer", "Bulgaria"),
        ("QA Engineer", "Estonia"),
        ("QA Engineer", "Lithuania"),
        ("QA Engineer", "Latvia"),
        ("QA Engineer", "Czechia"),
        ("QA Engineer", "Slovakia"),
        ("QA Engineer", "Hungary"),
        ("QA Engineer", "Croatia"),
        ("QA Engineer", "Serbia"),
        ("QA Engineer", "Greece"),
        # larger Western markets
        ("QA Engineer", "Spain"),
        ("QA Engineer", "Netherlands"),
        ("QA Engineer", "Germany"),
        ("QA Engineer", "Ireland"),
        ("QA Engineer", "United Kingdom"),
        ("QA Engineer", "Austria"),
        ("QA Engineer", "Sweden"),
        ("QA Engineer", "Denmark"),
        ("QA Engineer", "Finland"),
        ("QA Engineer", "Norway"),
        ("QA Engineer", "Switzerland"),
        ("QA Engineer", "Italy"),
        ("QA Engineer", "France"),
        ("QA Engineer", "Belgium"),
    ]

    out = []
    seen = set()

    plan = [(k, l, st, False) for k, l in armenia_searches for st in (0, 10, 25)]
    plan += [(k, l, 0, True) for k, l in remote_europe_searches]

    for keywords, location, start, remote_only in plan:
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               f"keywords={urllib.parse.quote(keywords)}"
               f"&location={urllib.parse.quote(location)}&start={start}")
        if remote_only:
            url += "&f_WT=2"
        try:
            raw = fetch_linkedin(url)
        except Exception as exc:
            print(f"  LinkedIn search skipped ({location}): {type(exc).__name__}")
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


SOURCES = [
    ("LinkedIn", parse_linkedin),
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
]


# --------------------------------------------------------------------------- #
# Filtering and scoring
# --------------------------------------------------------------------------- #

def haystack(job_):
    return f"{job_['title']} {job_['description']} {job_['location']}".lower()


def title_says_software_qa(job_) -> bool:
    title = job_["title"].lower()
    return any(re.search(p, title) for p in SOFTWARE_QA_TITLE_PATTERNS)


def is_scam(job_) -> bool:
    text = haystack(job_)
    if any(name in text for name in SCAM_BLOCKLIST):
        return True
    return any(phrase in text for phrase in SCAM_PHRASES)


def is_hard_excluded(job_) -> bool:
    text = haystack(job_)
    return any(term in text for term in HARD_EXCLUDE)


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

    if "native" in lang_block:
        for language in DJINNI_BAD_NATIVE:
            if language in lang_block:
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


def location_text(job_) -> str:
    """Everything the posting says about where it hires, lowercased."""
    return " ".join(job_["restrictions"] + [job_["location"]]).lower()


def is_armenia(job_) -> bool:
    return any(m in location_text(job_) for m in ARMENIA_MARKERS)


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


def remote_is_open(job_) -> str:
    """How open is this remote role? Returns 'worldwide', 'region', 'single' or ''.

    This replaces the old check, which looked for the word "remote" and therefore
    matched everything, including "Remote (UA)". Wording matters more than the
    presence of the word remote: "remote worldwide" and "remote, Poland only" are
    not the same job.
    """
    text = location_text(job_)
    if any(m in text for m in REMOTE_WORLDWIDE):
        return "worldwide"
    countries = named_countries(job_)
    if any(m in text for m in REMOTE_REGION) and not countries:
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

    1  Armenia, on-site or remote
    2  Remote and genuinely open, worldwide or regional
    3  Remote but one European country named
    4  On-site in Europe with visa sponsorship or relocation offered
    """
    # Tier 1: Armenia, no relocation, no visa, start immediately
    if is_armenia(job_):
        return 1

    if job_["remote"]:
        openness = remote_is_open(job_)
        if openness in ("worldwide", "region"):
            return 2
        if openness == "single":
            # A European country is named. Readable, but flag it.
            if set(named_countries(job_)) & set(EUROPE_COUNTRIES):
                return 3
        return None

    # On-site. Only worth a look when they offer to move him.
    if wants_sponsorship(job_):
        text = location_text(job_)
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


def job_key(job_):
    """Identity for the seen list.

    Company plus title, not URL. The same role gets posted on Djinni and on
    LinkedIn and on the company's own board, and the URLs have nothing in common,
    so a URL-based key lets the same job through three times under three names.
    The URL is still used as a tiebreaker when company and title are both empty.
    """
    company = re.sub(r"[^a-z0-9]", "", (job_.get("company") or "").lower())
    title = re.sub(r"[^a-z0-9]", "", (job_.get("title") or "").lower())
    if company and title:
        return f"{company}|{title}"
    return normalise_url(job_.get("url", ""))


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

def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "false",
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
    1: "🇦🇲 Armenia — on-site or remote",
    2: "🌍 Remote, open",
    3: "🌍 Remote, one country named",
    4: "🛂 On-site Europe, sponsorship offered",
}

TIER_NOTE = {
    1: "",
    2: "",
    3: "The posting names one country. Some of these will still take a contractor "
       "elsewhere, many will not. Worth one question before you spend time applying.",
    4: "On-site. They mention visa sponsorship or relocation, confirm the details "
       "before applying because the process takes months.",
}


def tier_note(job_, tier) -> str:
    return TIER_NOTE.get(tier, "")


def format_heartbeat(stats, new_count, sent_count, sources):
    """A short status line, so a quiet day is visibly quiet rather than suspicious.

    Without this, "no message" could mean three different things: no new jobs, a
    broken source, or a dead job. This says which.
    """
    lines = ["🔍 QA Job Radar ran", ""]
    lines.append(f"Scanned {stats.get('total', 0)} postings from {len(sources)} sources")
    lines.append(f"Passed filters: {stats.get('kept', 0)}")
    lines.append(f"New since last run: {new_count}")
    if sent_count:
        lines.append(f"Sent: {sent_count}")
    else:
        lines.append("Sent: none, everything new has already been posted")

    # Say plainly when a source returned nothing, because that is the failure that
    # hides best.
    dead = [name for name, count in sources.items() if count == 0]
    if dead:
        lines.append("")
        lines.append("Sources that returned nothing: " + ", ".join(dead))
    return "\n".join(lines)


def format_job(job_, tier):
    skills = ", ".join(matched_skills(job_)) or "general QA"
    if has_description(job_):
        fit_line = f"Fit: {relevance_score(job_)}%  ·  {skills}"
    else:
        fit_line = f"Fit: {relevance_score(job_)}% (title only)  ·  read the posting for detail"
    lines = [
        TIER_BADGE[tier],
        "",
        job_["title"],
        f"Company: {job_['company']}",
        f"Location: {job_['location']}",
        f"Source: {job_['source']}",
        fit_line,
    ]
    note = tier_note(job_, tier)
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines) + f"\n\nApply: {job_['url']}"


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
    """Same job posted on Staff.am and LinkedIn should reach the chat once."""
    return normalise_company(j["company"]) + "|" + normalise_title(j["title"])


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
             "out_of_scope": 0, "kept": 0}
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


def trim_to_caps(jobs):
    """Applies the per-tier caps, then rebuilds the send order.

    Kept in one place so --dry-run shows exactly what --once would send. Before this,
    the dry run printed the raw list and the two disagreed.
    """
    trimmed = []
    for tier_value in (1, 2, 3, 4):
        bucket = [j for j in jobs if j["tier"] == tier_value]
        trimmed.extend(bucket[:MAX_PER_TIER[tier_value]])
    trimmed.sort(key=lambda x: (x["tier"], -x["score"]))
    return trimmed


def run(dry_run=False, report=False, dump_json=False):
    seen = load_seen()
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
        for j in sorted(jobs, key=lambda x: (x["tier"], -x["score"])):
            print(f"[tier {j['tier']}] {j['score']}%  {j['title']}  @ {j['company']}  ({j['location']})")

    # --json and --report are inspection modes. They never send.
    if report or dump_json:
        return

    today = datetime.now(timezone.utc).date().isoformat()
    new = [j for j in jobs if job_key(j) not in seen]
    # Tier first, then score inside the tier.
    new.sort(key=lambda x: (x["tier"], -x["score"]))
    for j in new:
        seen[job_key(j)] = today

    if dry_run:
        to_send = trim_to_caps(new)
        counts = {}
        for j in to_send:
            counts[j["tier"]] = counts.get(j["tier"], 0) + 1
        print(f"\n{len(new)} new job(s) matched. {len(to_send)} would be sent:")
        for tier_value in (1, 2, 3, 4):
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
            caption = f"{j['title']} at {j['company']}"
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
    if os.getenv("JOB_RADAR_HEARTBEAT", "1") == "1":
        try:
            telegram_send(format_heartbeat(stats, len(new), sent, stats.get("sources", {})))
        except Exception as exc:
            print(f"Heartbeat failed: {exc}")

    # Persist exactly what went to Telegram, so the sheet gets the same list
    # rather than a fresh scan that might differ by a few postings.
    Path("sent_jobs.json").write_text(
        json.dumps([{k: v for k, v in j.items() if k != "description"} for j in sent_jobs],
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    # Sheet sync, in the same run. If it fails, the alerts still went out.
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_REFRESH_TOKEN"):
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from job_sheet_sync import sync_file
            added, skipped = sync_file("sent_jobs.json")
            print(f"Sheet: {added} added, {skipped} already there")
        except Exception as exc:
            print(f"Sheet sync skipped: {type(exc).__name__}: {exc}")
    else:
        print("Sheet sync skipped: no Google credentials in this run")

    save_seen(seen)
    print(f"Sent {sent} of {len(new)} new job(s).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="scan once and send to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="scan once, print only")
    parser.add_argument("--report", action="store_true", help="print everything that passed, grouped by tier")
    parser.add_argument("--json", action="store_true",
                        help="also write radar_jobs.json for job_sheet_sync.py")
    args = parser.parse_args()

    if args.report or args.json:
        run(report=args.report, dump_json=args.json)
    elif args.dry_run or args.once:
        run(dry_run=args.dry_run)
    else:
        print("Run with --once, --dry-run or --report. Nothing was sent.")
        parser.print_help()


if __name__ == "__main__":
    main()
