#!/usr/bin/env python3
"""Read job channels on Telegram and pull out the QA postings.

How this works, and what it cannot do.

Telegram has two ways in. A bot can only read a channel it has been added to as an
administrator, which needs the channel owner to do something. A user account can
read anything it is a member of, but that means storing a session credential for
Ahmad's own account in GitHub, and Telegram limits and flags accounts that read
at scale. Neither is right for an unattended job.

So this uses the third way: the public web preview at t.me/s/<channel>. Every
public channel has one, it needs no credentials, it cannot be rate limited into
an account ban, and it returns the last twenty posts as plain HTML. Paginating
with ?before=<id> walks further back.

The limit that follows from that: this only works for channels that are public
and have the preview enabled. A private or invite-only channel cannot be read
this way, and adding a bot as admin is the only way to cover those.

The channels are read from the TELEGRAM_CHANNELS repository variable, a comma
separated list of usernames without the @:

    TELEGRAM_CHANNELS = jobs_armenia,itjobs_yerevan,eu_remote_jobs

Locally, a channels.json next to this file works the same way:

    ["jobs_armenia", "itjobs_yerevan"]

Usage:
    python telegram_channels.py                 read the configured channels
    python telegram_channels.py --channel X     read one channel by name
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (compatible; QAJobRadar/2.0)"
PREVIEW = "https://t.me/s/{}"

# Words that mean this post is about a testing job. Armenian and Russian are in
# the list because the Yerevan channels post in all three languages, often the
# same role twice.
QA_WORDS = [
    "qa engineer", "qa automation", "qa specialist", "qa analyst", "qa lead",
    "quality assurance", "quality engineer", "test engineer", "test automation",
    "automation qa", "sdet", "manual tester", "software tester", "tester",
    "testing engineer", "тестировщик", "тестирование", "тест-инженер", "qa-инженер",
    "автотест", "թեստավորող", "թեսթավորող", "որակի ապահովման", "որակի մասնագետ",
]

# Posts that carry a QA word but are selling something.
NOT_A_JOB = [
    "course", "курс", "դասընթաց", "training", "bootcamp", "webinar", "вебинар",
    "mentorship", "подкаст", "podcast", "subscribe", "подпишись", "реклама",
    "advertisement", "resume review", "review my cv",
]

REMOTE_MARKERS = ["remote", "удaл", "удален", "удалён", "հեռավար", "work from home",
                  "remote-first", "anywhere"]

ARMENIA_MARKERS = ["armenia", "yerevan", "երևան", "հայաստան", "ереван", "армения"]
EUROPE_HINTS = [
    "poland", "portugal", "romania", "bulgaria", "estonia", "lithuania", "latvia",
    "czech", "slovakia", "hungary", "croatia", "serbia", "greece", "spain",
    "netherlands", "germany", "ireland", "united kingdom", "austria", "sweden",
    "denmark", "finland", "norway", "switzerland", "italy", "france", "belgium",
    "cyprus", "malta", "luxembourg", "slovenia", "georgia", "moldova", "poland",
    "eu ", "europe", "european union", "emea",
]


def configured_channels() -> list:
    raw = os.getenv("TELEGRAM_CHANNELS", "").strip()
    if raw:
        names = [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]
        return [n for n in names if n]
    local = Path(__file__).with_name("channels.json")
    if local.exists():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            return [str(c).strip().lstrip("@") for c in data if str(c).strip()]
        except Exception:
            return []
    return []


def fetch_channel(name: str, before: int | None = None) -> str:
    url = PREVIEW.format(name)
    if before:
        url += f"?before={before}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _strip_tags(fragment: str) -> str:
    # The split in parse_messages leaves the bubble's own attributes in front:
    #   js-message_text" dir="auto"><b>📑</b> Вакансия ...
    # so drop everything up to and including the first real closing bracket.
    if ">" in fragment:
        fragment = fragment.split(">", 1)[1]
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return html_module.unescape(text).strip()


def parse_messages(page: str, channel: str) -> list:
    """Returns one dict per post: id, text, links, date."""
    messages = []
    for block in page.split("tgme_widget_message_wrap")[1:]:
        post = re.search(r'data-post="([^"]+)"', block)
        date = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        if not post:
            continue
        # The text bubble ends where the footer begins. Splitting on the marker
        # rather than matching a closing div avoids cutting the text short when
        # the post contains nested formatting.
        text = ""
        if "tgme_widget_message_text" in block:
            chunk = block.split("tgme_widget_message_text", 1)[1]
            chunk = chunk.split("tgme_widget_message_footer", 1)[0]
            chunk = chunk.split("tgme_widget_message_reply", 1)[0]
            text = _strip_tags(chunk[:6000])
        links = [html_module.unescape(html_module.unescape(u))
                 for u in re.findall(r'href="(https?://[^"]+)"', block)]
        links = [u.replace("&amp;", "&") for u in links]
        links = [u for u in links
                 if "t.me/" not in u and "telegram.org" not in u and "telegram.me" not in u]
        messages.append({
            "id": post.group(1),
            "channel": channel,
            "text": text,
            "links": links,
            "date": date.group(1) if date else "",
        })
    return messages


def looks_like_qa_job(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    if not any(word in lowered for word in QA_WORDS):
        return False
    return not any(noise in lowered for noise in NOT_A_JOB)


FILLER = re.compile(
    r"^(вакансия|вакансія|#вакансия|#вакансія|job|job offer|hiring|vacancy|"
    r"աշխատանք|նոր աշխատանք|position|role|we are hiring|ищем|"
    r"private|публичная|confidential)\s*[:\-—]?\s*",
    re.I,
)


def guess_title(text: str) -> str:
    """The line that names the role, trimmed to something readable."""
    for line in text.splitlines():
        cleaned = FILLER.sub("", line.strip(" -•·|📑💎🔥💼🚀✅")).strip()
        if not cleaned or len(cleaned) < 4:
            continue
        lowered = cleaned.lower()
        if any(word in lowered for word in QA_WORDS) and len(cleaned) <= 90:
            return cleaned
    return "QA role"


def guess_location(text: str) -> tuple:
    """Returns (location, is_remote)."""
    lowered = (text or "").lower()
    remote = any(marker in lowered for marker in REMOTE_MARKERS)
    for marker in ARMENIA_MARKERS:
        if marker in lowered:
            return "Armenia", remote
    for hint in EUROPE_HINTS:
        if hint in lowered:
            return hint.strip().title(), remote
    return ("Remote" if remote else ""), remote


# Domains that are never the application link, even when the post puts them
# first. The iJob.am channel footer lists its own Facebook and Instagram before
# the vacancy, and the first version of this picked the Facebook group.
NOT_APPLY_DOMAINS = [
    "facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "linkedin.com/company", "linkedin.com/in",
    "t.me", "telegram.org", "telegram.me", "wa.me", "whatsapp.com",
]

# Domains and path fragments that almost always mean a real vacancy page.
JOB_LINK_HINTS = [
    "/jobs/", "/job/", "/vacancy/", "/vacancies/", "/careers/", "/career/",
    "/apply", "/position", "/opening", "/hire", "lever.co", "greenhouse.io",
    "workable.com", "huntflow", "teamtailor", "smartrecruiters", "recruitee",
    "breezy.hr", "personio", "join.com", "jobs.", "careers.",
]


def pick_apply_link(links: list) -> str:
    """The link a human would actually click.

    A Telegram post often carries several: the vacancy, the channel's own social
    pages, a contact. Score them instead of taking the first one.
    """
    scored = []
    for link in links:
        lowered = link.lower()
        if any(bad in lowered for bad in NOT_APPLY_DOMAINS):
            continue
        score = sum(1 for hint in JOB_LINK_HINTS if hint in lowered)
        scored.append((score, link))
    if not scored:
        return ""
    scored.sort(key=lambda pair: -pair[0])
    return scored[0][1]


COMPANY_PREFIXES = ["company:", "компания:", "ընկերություն:", "🏢", "🏭", "employer:"]


def guess_company(text: str) -> str:
    for line in text.splitlines():
        lowered = line.lower().strip()
        for prefix in COMPANY_PREFIXES:
            if lowered.startswith(prefix.strip()):
                value = line.split(":", 1)[-1] if ":" in line else line
                # Drop leading emoji and markers, keep the name.
                value = re.sub(r"^[^\w]+", "", value).strip()
                value = re.sub(r"^(company|компания|ընկերություն)\s*", "", value, flags=re.I)
                return value.strip(" :-")[:60] or "Not listed"
    return "Not listed"


def message_to_job(message: dict) -> dict:
    text = message["text"]
    location, remote = guess_location(text)
    apply_url = pick_apply_link(message["links"]) or f"https://t.me/{message['id']}"
    return {
        "source": f"Telegram / {message['channel']}",
        "title": guess_title(text),
        "company": guess_company(text),
        "location": location or "Not specified",
        "url": apply_url,
        "description": text[:4000],
        "published": message["date"],
        "remote": remote,
        "restrictions": [location] if location else [],
        "relocation": "relocation" in text.lower() or "релокац" in text.lower(),
    }


def parse_telegram_channels(pages: int = 1) -> list:
    """The function the radar calls as a source."""
    channels = configured_channels()
    if not channels:
        return []
    jobs = []
    for name in channels:
        try:
            page = fetch_channel(name)
        except Exception as exc:
            print(f"  telegram/{name}: skipped ({type(exc).__name__})")
            continue
        messages = parse_messages(page, name)
        kept = [m for m in messages if looks_like_qa_job(m["text"])]
        jobs.extend(message_to_job(m) for m in kept)
        print(f"  telegram/{name}: {len(messages)} posts, {len(kept)} QA postings")
    return jobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", help="read one channel, ignoring the config")
    args = parser.parse_args()

    if args.channel:
        page = fetch_channel(args.channel.lstrip("@"))
        messages = parse_messages(page, args.channel.lstrip("@"))
        print(f"{len(messages)} posts in {args.channel}")
        for message in messages:
            mark = "QA " if looks_like_qa_job(message["text"]) else "---"
            print(f"  [{mark}] {message['id']:24s} {message['text'][:70].replace(chr(10), ' ')}")
        return

    channels = configured_channels()
    if not channels:
        print("No channels configured.")
        print("Set TELEGRAM_CHANNELS to a comma separated list of public channel names,")
        print("or put a channels.json next to this file.")
        return
    jobs = parse_telegram_channels()
    print(f"\n{len(jobs)} QA posting(s):")
    for job in jobs:
        print(f"  {job['title'][:44]:44s} | {job['location'][:18]:18s} | {job['url'][:50]}")


if __name__ == "__main__":
    main()
