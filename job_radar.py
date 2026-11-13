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
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path(__file__).with_name("seen_jobs.json")
USER_AGENT = "Mozilla/5.0 (compatible; QAJobRadar/2.0)"

# Hard cap per run so a backlog never floods the chat.
MAX_PER_RUN = 8


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
    "nederlands", "dutch required", "fluent dutch", "estonian required",
    "fluent french", "french required", "fluent spanish required",
    "italiano madrelingua", "polski wymagany",
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

# A title written in these languages is a strong sign the interview is too.
FOREIGN_TITLE_HINTS = [
    "m/w/d", "(m/w/x)", "w/m/d", "mit", "und", "für", "entwickler",
    "tester (m", "referent", "spezialist", "ingenieur", "ingénieur",
    "desenvolvedor", "ingegnere", "inżynier", "vývojář",
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

# Places that mean the job is open to someone sitting in Yerevan.
REMOTE_OPEN_MARKERS = [
    "worldwide", "anywhere", "global", "emea", "europe", "european",
    "eu only", "eu-based", "european union", "remote", "international",
    "any country", "anywhere in the world",
]

EUROPE_COUNTRIES = [
    "armenia", "georgia", "poland", "germany", "netherlands", "belgium",
    "luxembourg", "france", "spain", "portugal", "italy", "austria",
    "switzerland", "czech", "czechia", "slovakia", "hungary", "romania",
    "bulgaria", "croatia", "serbia", "slovenia", "greece", "cyprus", "malta",
    "estonia", "latvia", "lithuania", "sweden", "norway", "denmark",
    "finland", "iceland", "ireland", "united kingdom", "uk", "poland",
    "ukraine", "moldova", "montenegro", "albania", "bosnia", "macedonia",
]

SPONSORSHIP_MARKERS = [
    "visa sponsorship", "sponsorship available", "we sponsor", "sponsor visa",
    "work permit", "relocation package", "relocation support", "relocation bonus",
    "paid relocation", "relocation assistance", "we relocate", "blue card",
    "eu blue card", "skilled worker visa", "tier 2", "certificate of sponsorship",
    "relocation offered", "visa support",
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


def open_to_armenia(job_) -> bool:
    """Can someone sitting in Yerevan take this job without moving country?"""
    if job_["source"] == "Staff.am":
        return True
    text = " ".join(job_["restrictions"] + [job_["location"]]).lower()
    if not text.strip():
        return not job_["remote"] is False  # unknown + remote counts
    if any(marker in text for marker in REMOTE_OPEN_MARKERS):
        return True
    # A restriction list naming Europe or Armenia
    return any(country in text for country in EUROPE_COUNTRIES)


def is_armenia(job_) -> bool:
    text = " ".join([job_["location"]] + job_["restrictions"]).lower()
    return "armenia" in text or "yerevan" in text


def wants_sponsorship(job_) -> bool:
    text = haystack(job_)
    return job_["relocation"] or any(marker in text for marker in SPONSORSHIP_MARKERS)


def assign_tier(job_):
    """Returns 1, 2, 3 or None."""
    if is_armenia(job_):
        return 2
    if job_["remote"] and open_to_armenia(job_):
        return 1
    if not job_["remote"] and wants_sponsorship(job_):
        # On-site, but they say they will move you.
        return 3
    if job_["remote"] and not job_["restrictions"]:
        return 1
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
    return max(20, min(score, 97))


def matched_skills(job_):
    text = haystack(job_)
    return [s for s in dict.fromkeys(QA_SKILLS) if s in text][:8]


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def job_key(job_):
    return job_["url"] or f"{job_['source']}|{job_['title']}|{job_['company']}"


def load_seen():
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(list(seen)[-3000:], ensure_ascii=False, indent=2))


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


NEEDS_CHECK_COUNTRIES = [
    "ua", "ukraine", "poland", "pl", "germany", "de", "spain", "es",
    "portugal", "pt", "italy", "it", "belgium", "be", "france", "fr",
]


def location_caveat(job_) -> str:
    """Returns a warning line when the posting restricts who it will hire."""
    text = " ".join(job_["restrictions"] + [job_["location"]]).lower()
    if not text.strip():
        return ""
    if any(m in text for m in ("worldwide", "anywhere", "global", "emea", "europe")):
        return ""
    if is_armenia(job_):
        return ""
    if any(c in text for c in NEEDS_CHECK_COUNTRIES):
        return "Check the location rule: this one names a country. Some of these hire across Eastern Europe, some do not."
    return ""


TIER_BADGE = {
    1: "🥇 Remote — open to Europe",
    2: "🥈 Armenia",
    3: "🥉 On-site EU — sponsorship mentioned",
}


def format_job(job_, tier):
    skills = ", ".join(matched_skills(job_)) or "general QA"
    lines = [
        f"{TIER_BADGE[tier]}",
        "",
        job_["title"],
        f"Company: {job_['company']}",
        f"Location: {job_['location']}",
        f"Source: {job_['source']}",
        f"Fit: {relevance_score(job_)}%  ·  {skills}",
    ]
    caveat = location_caveat(job_)
    if caveat:
        lines.append(caveat)
    if tier == 3:
        lines.append("On-site. They mention visa or relocation support, confirm it before applying.")
    return "\n".join(lines) + f"\n\nApply: {job_['url']}"


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

def collect():
    all_jobs = []
    for name, parser in SOURCES:
        try:
            found = parser()
            all_jobs.append((name, found))
            print(f"{name}: {len(found)} fetched")
        except Exception as exc:
            print(f"{name}: skipped ({exc.__class__.__name__}: {exc})")

    kept = []
    stats = {"total": 0, "not_qa": 0, "scam_blocked": 0, "hard_excluded": 0,
             "language": 0, "out_of_scope": 0, "kept": 0}
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
            tier = assign_tier(j)
            if tier is None:
                stats["out_of_scope"] += 1
                continue
            j["tier"] = tier
            j["score"] = relevance_score(j)
            kept.append(j)
            stats["kept"] += 1
    print("Filter stats:", stats)
    return kept, stats


def run(dry_run=False, report=False):
    seen = load_seen()
    jobs, stats = collect()

    if report:
        print(f"\n{len(jobs)} jobs passed the filters\n")
        for j in sorted(jobs, key=lambda x: (x["tier"], -x["score"])):
            print(f"[tier {j['tier']}] {j['score']}%  {j['title']}  @ {j['company']}  ({j['location']})")
        return

    new = [j for j in jobs if job_key(j) not in seen]
    # Tier first, then score inside the tier.
    new.sort(key=lambda x: (x["tier"], -x["score"]))
    for j in new:
        seen.add(job_key(j))

    if dry_run:
        print(f"\n{len(new)} new job(s) would be sent\n")
        for j in new[:MAX_PER_RUN]:
            print("=" * 60)
            print(format_job(j, j["tier"]))
            print()
        return

    sent = 0
    for j in new[:MAX_PER_RUN]:
        try:
            telegram_send(format_job(j, j["tier"]))
            sent += 1
            time.sleep(1)
        except Exception as exc:
            print(f"Send failed for {j['title']}: {exc}")
    if len(new) > MAX_PER_RUN:
        try:
            telegram_send(f"ℹ️ {len(new) - MAX_PER_RUN} more matched. They will come through on the next run.")
        except Exception:
            pass
    save_seen(seen)
    print(f"Sent {sent} of {len(new)} new job(s).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="scan once and send to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="scan once, print only")
    parser.add_argument("--report", action="store_true", help="print everything that passed, grouped by tier")
    args = parser.parse_args()

    if args.report:
        run(report=True)
    elif args.dry_run or args.once:
        run(dry_run=args.dry_run)
    else:
        print("Run with --once, --dry-run or --report. Nothing was sent.")
        parser.print_help()


if __name__ == "__main__":
    main()
