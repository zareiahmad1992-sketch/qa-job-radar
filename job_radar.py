#!/usr/bin/env python3
"""QA Job Radar: public job sources -> Telegram.

The script never stores the Telegram token. Set TELEGRAM_BOT_TOKEN in the shell.
Run once for a test, then run without --once to poll every 10 minutes.
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
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_SECONDS = int(os.getenv("JOB_RADAR_POLL_SECONDS", "600"))
STATE_FILE = Path(__file__).with_name("seen_jobs.json")
USER_AGENT = "AhmadQAJobRadar/1.0 (+public-job-feeds)"

INCLUDE_TERMS = [
    "qa", "quality assurance", "software tester", "test engineer", "sdet",
    "manual qa", "automation qa", "qa automation", "api tester", "api qa",
    "mobile qa", "software quality", "test automation", "quality engineer",
]
EXCLUDE_TERMS = [
    "medical assistant", "nursing", "nurse", "sales qa", "quality control manager",
    "food quality", "manufacturing quality", "warehouse quality",
]
RESUME_TERMS = [
    "playwright", "selenium", "appium", "postman", "swagger", "python",
    "javascript", "sql", "gitlab", "github actions", "jira", "testrail",
    "xray", "jmeter", "api", "mobile", "web", "regression", "integration",
]
EUROPE_TERMS = [
    "europe", "european", "emea", "e.u.", "eu", "worldwide", "global", "anywhere",
    "uk", "united kingdom", "ireland", "germany", "france", "netherlands", "belgium",
    "luxembourg", "switzerland", "austria", "italy", "spain", "portugal", "poland",
    "czech", "czechia", "slovakia", "hungary", "romania", "bulgaria", "croatia",
    "serbia", "slovenia", "greece", "cyprus", "malta", "estonia", "latvia", "lithuania",
    "sweden", "norway", "denmark", "finland", "iceland", "ukraine", "georgia", "armenia",
]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
    with urllib.request.urlopen(req, timeout=30) as response:
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


def make_job(source, title, company, location, url, description="", published=""):
    title = clean_text(title)
    company = clean_text(company) or "Not listed"
    location = clean_text(location) or "Remote / not specified"
    description = clean_text(description)
    url = html.unescape(str(url or "")).strip()
    if url.startswith("/"):
        url = "https://staff.am" + url
    return {
        "source": source,
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": description,
        "published": clean_text(published),
    }


def parse_remoteok():
    data = fetch_json("https://remoteok.com/api")
    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        job = make_job(
            "Remote OK", item.get("position"), item.get("company"), item.get("location"),
            item.get("url") or ("https://remoteok.com/remote-jobs/" + item.get("slug", "")),
            item.get("description"), item.get("date"),
        )
        job["remote"] = True
        jobs.append(job)
    return jobs


def parse_remotive():
    data = fetch_json("https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
    jobs = []
    for item in data.get("jobs", []):
        job = make_job(
            "Remotive", item.get("title"), item.get("company_name"),
            item.get("candidate_required_location"), item.get("url"),
            item.get("description"), item.get("publication_date"),
        )
        job["remote"] = True
        jobs.append(job)
    return jobs


def parse_jobicy():
    data = fetch_json("https://jobicy.com/api/v2/remote-jobs?count=50")
    jobs = []
    for item in data.get("jobs", []):
        job = make_job(
            "Jobicy", item.get("jobTitle"), item.get("companyName"),
            item.get("jobGeo") or item.get("jobLevel"), item.get("url") or item.get("jobUrl"),
            item.get("jobDescription"), item.get("pubDate"),
        )
        job["remote"] = True
        jobs.append(job)
    return jobs


class StaffLinkParser(HTMLParser):
    """Extract job links/titles from the public Staff.am QA category page."""
    def __init__(self):
        super().__init__()
        self.current_href = None
        self.capture_depth = 0
        self.title_parts = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if tag == "a" and href.startswith("/en/jobs/quality-assurance/"):
            self.current_href = href
            self.capture_depth = 1
            self.title_parts = []
        elif self.current_href:
            self.capture_depth += 1

    def handle_data(self, data):
        if self.current_href:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if self.current_href:
            self.capture_depth -= 1
            if self.capture_depth <= 0:
                title = clean_text(" ".join(self.title_parts))
                if title and self.current_href:
                    self.items.append((self.current_href, title))
                self.current_href = None
                self.title_parts = []


LINKEDIN_SEARCHES = [
    ("QA Engineer", "Europe", "2"),
    ("QA Automation Engineer", "Europe", "2"),
    ("Manual QA", "Europe", "2"),
    ("API QA", "Europe", "2"),
    ("QA Engineer", "Armenia", "1"),
]


def parse_linkedin():
    """Read public, no-login LinkedIn job-search pages.

    f_WT=2 is LinkedIn's public remote-work filter and f_WT=1 is on-site.
    This does not log in, use cookies, or access the user's account.
    """
    jobs = []
    card_re = re.compile(r'<div[^>]+class="[^"]*job-search-card[^"]*".*?</li>', re.I | re.S)
    link_re = re.compile(r'<a[^>]+class="[^"]*base-card__full-link[^"]*"[^>]+href="([^"]+)"', re.I | re.S)
    title_re = re.compile(r'<h3[^>]*>(.*?)</h3>', re.I | re.S)
    company_re = re.compile(r'<h4[^>]*>(.*?)</h4>', re.I | re.S)
    location_re = re.compile(r'<span[^>]+class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>', re.I | re.S)
    date_re = re.compile(r'<time[^>]+datetime="([^"]+)"[^>]*>(.*?)</time>', re.I | re.S)
    for keywords, location, work_type in LINKEDIN_SEARCHES:
        params = urllib.parse.urlencode({
            "keywords": keywords,
            "location": location,
            "f_TPR": "r86400",
            "f_WT": work_type,
        })
        url = "https://www.linkedin.com/jobs/search/?" + params
        try:
            raw = fetch(url).decode("utf-8", "replace")
        except Exception as exc:
            print(f"LinkedIn ({keywords}, {location}): skipped ({exc})")
            continue
        for card in card_re.findall(raw):
            link_match = link_re.search(card)
            title_match = title_re.search(card)
            if not link_match or not title_match:
                continue
            link = html.unescape(link_match.group(1)).split("?")[0]
            title = clean_text(title_match.group(1))
            company_match = company_re.search(card)
            location_match = location_re.search(card)
            date_match = date_re.search(card)
            company = clean_text(company_match.group(1)) if company_match else "Not listed"
            job_location = clean_text(location_match.group(1)) if location_match else location
            published = clean_text(date_match.group(2)) if date_match else ""
            job = make_job(
                "LinkedIn", title, company, job_location, link,
                f"Public LinkedIn search result for {keywords} in {location}", published,
            )
            job["remote"] = work_type == "2"
            job["scope"] = "europe_remote" if work_type == "2" else "armenia_onsite"
            jobs.append(job)
    return jobs


def parse_staff():
    raw = fetch("https://staff.am/en/jobs/quality-assurance").decode("utf-8", "replace")
    parser = StaffLinkParser()
    parser.feed(raw)
    seen = set()
    jobs = []
    for href, title in parser.items:
        if href in seen:
            continue
        seen.add(href)
        # Staff.am exposes the work mode on each job detail page. Keep
        # Armenian on-site roles for this radar; European roles come from
        # the remote sources below.
        try:
            detail = fetch("https://staff.am" + href).decode("utf-8", "replace")
            remote_match = re.search(r'"is_remote":(true|false)', detail)
            is_remote = remote_match and remote_match.group(1) == "true"
        except Exception:
            is_remote = None
        if is_remote is not False:
            continue
        job = make_job("Staff.am", title, "Armenia employer", "Yerevan / Armenia (on-site)", href)
        job["remote"] = False
        jobs.append(job)
    return jobs


def is_relevant(job):
    title = job['title'].lower()
    text = f"{job['title']} {job['description']}".lower()
    if any(term in text for term in EXCLUDE_TERMS):
        return False
    # Require a QA/testing signal in the job title. This prevents unrelated
    # jobs whose descriptions merely mention testing or APIs from being sent.
    title_terms = [
        'qa', 'quality assurance', 'software tester', 'test engineer', 'sdet',
        'manual test', 'automation test', 'test automation', 'quality engineer',
    ]
    return any(term in title for term in title_terms)


def in_requested_scope(job):
    # Requested scope: on-site Armenia + remote jobs open to Europe.
    if job["source"] == "Staff.am":
        return job.get("remote") is False and "armenia" in job["location"].lower()
    if job["source"] == "LinkedIn":
        return job.get("scope") == "europe_remote" or job.get("scope") == "armenia_onsite"
    if job.get("remote") is not True:
        return False
    location = job["location"].lower()
    return any(term in location for term in EUROPE_TERMS)


def preliminary_fit(job):
    text = f"{job['title']} {job['description']}".lower()
    matched = [term for term in RESUME_TERMS if term in text]
    score = min(99, 45 + len(matched) * 5)
    if any(x in text for x in ("yerevan", "armenia", "remote", "worldwide", "emea", "europe")):
        score += 5
    return min(score, 99), matched[:8]


def job_key(job):
    return job["url"] or (job["source"] + "|" + job["title"] + "|" + job["company"])


def load_seen():
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    # Keep the state file compact while retaining the newest-ish 2,000 keys.
    STATE_FILE.write_text(json.dumps(list(seen)[-2000:], ensure_ascii=False, indent=2))


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "false"}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result)


def format_job(job):
    score, matched = preliminary_fit(job)
    matched_text = ", ".join(matched) if matched else "general QA keywords"
    scope_label = "🇦🇲 Armenia / On-site" if (job["source"] == "Staff.am" or job.get("scope") == "armenia_onsite") else "🌍 Europe / Remote"
    return (
        "🆕 QA Job Radar\n\n"
        f"{job['title']}\n"
        f"Type: {scope_label}\n"
        f"Company: {job['company']}\n"
        f"Location: {job['location']}\n"
        f"Source: {job['source']}\n"
        f"Preliminary fit: {score}%\n"
        f"Matched keywords: {matched_text}\n\n"
        f"Apply: {job['url']}"
    )


def collect_jobs():
    sources = [
        ("LinkedIn", parse_linkedin),
        ("Remote OK", parse_remoteok),
        ("Remotive", parse_remotive),
        ("Jobicy", parse_jobicy),
        ("Staff.am", parse_staff),
    ]
    all_jobs = []
    for name, parser in sources:
        try:
            all_jobs.extend(parser())
            print(f"{name}: loaded")
        except Exception as exc:
            print(f"{name}: skipped ({exc})")
    return [job for job in all_jobs if is_relevant(job) and in_requested_scope(job) and job.get("url")]


def run_once(dry_run=False):
    seen = load_seen()
    jobs = collect_jobs()
    new_jobs = []
    for job in jobs:
        key = job_key(job)
        if key not in seen:
            new_jobs.append(job)
            seen.add(key)
    # Avoid flooding the first run; send the 10 most relevant-looking new jobs.
    new_jobs.sort(key=lambda j: preliminary_fit(j)[0], reverse=True)
    if dry_run:
        print(f"Found {len(jobs)} relevant jobs; {len(new_jobs)} are new.")
        for job in new_jobs[:10]:
            print("\n" + format_job(job))
    else:
        for job in new_jobs[:10]:
            telegram_send(format_job(job))
            time.sleep(1)
        if len(new_jobs) > 10:
            telegram_send(f"ℹ️ {len(new_jobs) - 10} more new QA jobs were found. They will be checked on the next run.")
        print(f"Sent {min(len(new_jobs), 10)} new job(s).")
    if not dry_run:
        save_seen(seen)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="Scan and print; do not send Telegram messages")
    args = parser.parse_args()
    if args.once or args.dry_run:
        run_once(dry_run=args.dry_run)
        return
    print(f"QA Job Radar running every {POLL_SECONDS} seconds. Press Ctrl+C to stop.")
    while True:
        try:
            run_once(dry_run=False)
        except Exception as exc:
            print(f"Scan error: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
