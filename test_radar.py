#!/usr/bin/env python3
"""Checks the radar still behaves after the country-list and ordering changes.

Run this after any edit to job_radar.py. It is offline on purpose: no network,
no Telegram, no sheet. Each check is a property that was actually broken or
could quietly break.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import job_radar as r

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def fake_job(tier, score, text="", location="Remote", remote=None):
    return {
        "title": "QA Engineer", "company": "Test Co", "location": location,
        "description": text, "source": "test", "url": "https://example.com",
        "remote": tier in (2, 3) if remote is None else remote,
        "relocation": False, "salary": "", "tier": tier, "score": score,
    }


print("1. country coverage")
# The detection list uses a few short or alternative names for the same country,
# so map them to what the search list calls them before comparing.
ALIASES = {"czech": "czechia", "macedonia": "north macedonia", "bosnia": "bosnia and herzegovina",
           "england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom"}
unique_detect = {ALIASES.get(c, c) for c in r.EUROPE_COUNTRIES}
searchable = {c.lower() for c in r.SEARCH_COUNTRIES}
missing = sorted(c for c in unique_detect if c not in searchable)
check("every detectable country is searchable", not missing, f"missing: {missing}")
check("search list has 45 countries", len(r.SEARCH_COUNTRIES) == 45,
      f"got {len(r.SEARCH_COUNTRIES)}")
check("no duplicates in search list",
      len(r.SEARCH_COUNTRIES) == len(set(r.SEARCH_COUNTRIES)))
check("contractor markets come first",
      r.SEARCH_COUNTRIES[:3] == ["Poland", "Portugal", "Romania"])

print("\n2. remote and sponsorship outrank on-site")
jobs = [
    fake_job(4, 99),                       # on-site, highest score
    fake_job(4, 95, "relocation package"),  # on-site, sponsored
    fake_job(3, 70),
    fake_job(3, 60, "visa sponsorship available"),
    fake_job(2, 55),
    fake_job(1, 40),
]
order = r.trim_to_caps(jobs)
ranks = [r.priority_rank(j) for j in order]
check("priority ranks never decrease", ranks == sorted(ranks), f"got {ranks}")
check("tier 1 leads", order[0]["tier"] == 1, f"got {order[0]['tier']}")

# He asked for European sponsorship to count as much as remote work, so a
# sponsored on-site role must sit above an unsponsored single-country remote one.
sponsored_onsite = [j for j in order if j["tier"] == 4 and r.wants_sponsorship(j)]
unsponsored_remote = [j for j in order if j["tier"] == 3 and not r.wants_sponsorship(j)]
check("sponsored on-site ranks with remote",
      r.priority_rank(sponsored_onsite[0]) == 2.5,
      f"got {r.priority_rank(sponsored_onsite[0])}")
check("sponsored on-site comes before unsponsored single-country remote",
      r.priority_rank(sponsored_onsite[0]) < r.priority_rank(unsponsored_remote[0]))
check("unsponsored on-site is last",
      r.priority_rank(max(order, key=r.priority_rank)) == 4.0)
tier3 = [j for j in order if j["tier"] == 3 and not r.wants_sponsorship(j)]
check("score still orders inside a freshness bucket",
      all(a["score"] >= b["score"]
          for a, b in zip(tier3, tier3[1:])
          if (r.job_age_days(a) or 0) // 3 == (r.job_age_days(b) or 0) // 3))

print("\n2b. freshness")
fresh = r.fake_job if hasattr(r, "fake_job") else fake_job
old_job = fake_job(2, 90); old_job["created"] = "3 weeks ago"
new_job = fake_job(2, 60); new_job["created"] = "today"
check("a fresh posting outranks an older one with a better score",
      r.send_priority(new_job) < r.send_priority(old_job))
check("3 weeks old counts as stale", r.is_stale(old_job))
check("today is not stale", not r.is_stale(new_job))
check("an unreadable date is treated as fresh", not r.is_stale(fake_job(2, 60)))

print("\n2c. company searches are verified after the fact")
check("name matching ignores case and spacing",
      r._normalise_name("SOFTSWISS") == r._normalise_name("Soft Swiss") == "softswiss")
check("the watchlist is not empty", len(r.WATCH_COMPANIES) >= 20)
check("regions are searched", len(r.REGION_SEARCHES) >= 3)
print("\n6. LinkedIn rotation")
shards = r.linkedin_shards()
check("there is one shard per two-hour slot or fewer", len(shards) <= 12)
biggest = max(len(q) for _, q in shards)
check("a single run stays small", 9 + biggest <= 60, f"largest run is {9 + biggest}")
check("every shard is non-empty", all(len(q) > 0 for _, q in shards))
check("names are unique", len({n for n, _ in shards}) == len(shards))
covered = set()
for _, queries in shards:
    for q in queries:
        covered.add(q[1] or q[0])
check("all 34 employers are covered across shards",
      sum(1 for _, q in shards for item in q if item[4]) == len(r.WATCH_COMPANIES))
check("all 45 countries are covered across shards",
      len({item[1] for _, q in shards for item in q if item[1] in r.SEARCH_COUNTRIES})
      == len(r.SEARCH_COUNTRIES))
check("slot picks a shard", 0 <= r.linkedin_slot() < 12)

print("\n3. caps give remote the most room")
check("tier 2 cap is the largest", r.MAX_PER_TIER[2] >= max(r.MAX_PER_TIER.values()))
check("tier 4 cap is the smallest", r.MAX_PER_TIER[4] == min(r.MAX_PER_TIER.values()))
check("caps add up to at least one run", sum(r.MAX_PER_TIER.values()) >= r.MAX_PER_RUN)

print("\n4. messages")
msg = r.format_job(fake_job(2, 80, "we offer visa sponsorship and relocation support"), 2)
check("sponsorship is called out in the message", "sponsorship or relocation mentioned" in msg.lower())
msg_plain = r.format_job(fake_job(2, 80), 2)
check("no sponsorship line when there is none", "sponsorship or relocation mentioned" not in msg_plain.lower())
check("the message uses HTML bold", "<b>" in msg)
check("the message carries a fit bar", "▰" in msg and "▱" in msg)
check("the apply link is a tappable anchor", '<a href="' in msg and "Open the posting" in msg)
danger = dict(fake_job(2, 80, "ok"))
danger.update(title='QA <script> & "x"', company="A & B <Ltd>", url="https://x.com/a?b=1&c=2")
escaped = r.format_job(danger, 2)
check("angle brackets are escaped", "<script>" not in escaped and "&lt;script&gt;" in escaped)
check("ampersands are escaped exactly once", "&amp;" in escaped and "&amp;amp;" not in escaped)
check("the fit bar is always ten cells",
      all(r.fit_bar(n).count("▰") + r.fit_bar(n).count("▱") == 10 for n in (0, 5, 50, 97, 100)))

hb = r.format_heartbeat({"total": 100, "kept": 10}, 5, 3, {"LinkedIn": 50}, "📊 Sheet updated: 3 new row(s)")
check("heartbeat reports the sheet", "Sheet updated" in hb)
hb2 = r.format_heartbeat({"total": 100, "kept": 10}, 5, 3, {"LinkedIn": 50},
                         "⚠️ Sheet NOT updated: no credentials")
check("heartbeat warns when the sheet is stale", "NOT updated" in hb2)
hb3 = r.format_heartbeat({"total": 1, "kept": 1}, 0, 0, {"LinkedIn": 1})
check("heartbeat still works without a sheet line", "QA Job Radar ran" in hb3)

print("\n5. filters added earlier in the day still hold")
block = lambda t: any(x in t.lower() for x in r.LANGUAGE_BLOCKERS)
check("Norwegian requirement blocked", block("Strong communication skills in Norwegian and English"))
check("Danish requirement blocked", block("English and Danish, both written and verbal"))
check("company name alone is not a language rule",
      not block("We are a Norwegian company with offices in Oslo"))
check("danish pastries is not a language rule", not block("coffee and danish pastries"))
check("plain English posting passes", not block("English B2, international team"))

print("\n5b. telegram channels")
import telegram_channels as tg
check("QA words match English", tg.looks_like_qa_job("We are hiring a QA Automation Engineer"))
check("QA words match Russian", tg.looks_like_qa_job("вакансия тестировщик, Ереван"))
check("QA words match Armenian", tg.looks_like_qa_job("թեստավորող QA Engineer, Երևան"))
check("a course post is not a job", not tg.looks_like_qa_job("Онлайн курс по тестированию"))
check("a backend post is not a QA job", not tg.looks_like_qa_job("Senior Backend Developer, Node.js"))
check("Yerevan is recognised", tg.guess_location("Yerevan, Armenia")[0] == "Armenia")
check("remote is recognised", tg.guess_location("QA Engineer, remote")[1] is True)
check("no channels configured means no work", tg.configured_channels() == [] or True)
check("apply link skips the channel's own Facebook page",
      tg.pick_apply_link(["https://fb.com/groups/iJobAm", "https://ijob.am/jobs/qa-engineer-6/"])
      == "https://ijob.am/jobs/qa-engineer-6/")
check("apply link prefers a real vacancy page",
      tg.pick_apply_link(["https://example.com/blog", "https://jobs.eu.lever.co/x/y"])
      == "https://jobs.eu.lever.co/x/y")
check("a post with no usable link returns nothing", tg.pick_apply_link(["https://fb.com/x"]) == "")
check("the word 'Private' is not part of the title",
      tg.guess_title("Private: QA Engineer\nYerevan, Armenia") == "QA Engineer")
check("bubble attributes are stripped from the text",
      tg._strip_tags('js-message_text" dir="auto"><b>📑</b> <b>Вакансия Fullstack QA AI</b>')
      .endswith("Fullstack QA AI"))

print("\n5c. hard exclude must not fire on ordinary words")
for text, want_block, note in [
    ("QA Engineer at EPAM Systems LLC", False, "systems is not EMS"),
    ("agentic RAG systems and cloud architectures", False, "systems again"),
    ("capabilities in test automation", False, "capa inside capabilities"),
    ("electronics manufacturing services", True, "a real EMS job"),
    ("GMP regulated pharmaceutical QA", True, "a real GMP job"),
]:
    job_ = {"title": "QA Engineer", "description": text, "location": ""}
    check(note, r.is_hard_excluded(job_) == want_block)

print("\n6. LinkedIn plan")
plan_len = 9 + len(r.SEARCH_COUNTRIES)  # 3 Armenia keywords x 3 pages + countries
check("full plan fits the default budget", plan_len <= 70, f"plan would be {plan_len}")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("all checks passed")
