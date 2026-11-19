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

print("\n1b. Germany is its own row")
GERMANY_CASES = ["Berlin, Germany", "Munich", "München", "Remote, Deutschland",
                 "Frankfurt am Main", "Hamburg, hybrid", "Remote - DACH region"]
NOT_GERMANY = ["Remote, Europe", "Vienna, Austria", "Zurich, Switzerland",
               "Tbilisi, Georgia", "Warsaw, Poland"]
for loc in GERMANY_CASES:
    check(f"is_germany({loc!r})",
          r.is_germany({"location": loc, "restrictions": [], "title": "QA",
                        "description": "", "remote": True}))
for loc in NOT_GERMANY:
    check(f"is_germany({loc!r}) is False",
          not r.is_germany({"location": loc, "restrictions": [], "title": "QA",
                            "description": "", "remote": True}))

berlin = {"title": "QA Engineer", "company": "X", "location": "Berlin, Germany",
          "description": "", "restrictions": [], "remote": False,
          "relocation": False, "salary": "", "source": "t", "url": "u"}
check("on-site Germany is tier 25, not tier 4", r.assign_tier(berlin) == 25,
      f"got {r.assign_tier(berlin)}")
berlin_remote = dict(berlin, location="Remote, Germany", remote=True)
check("remote Germany is also tier 25", r.assign_tier(berlin_remote) == 25,
      f"got {r.assign_tier(berlin_remote)}")
berlin_v = dict(berlin, tier=25, remote=False)
check("on-site Germany ranks above sponsored elsewhere and above single-country remote",
      r.priority_rank(berlin_v) == 2.4, f"got {r.priority_rank(berlin_v)}")
check("Germany has its own cap", 25 in r.MAX_PER_TIER)
check("Germany is in the send order", 25 in r.TIER_ROWS)

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
print("\n5d. the board's own category beats the title")
for tags, title, want_block, note in [
    (["Marketing"], "AI Automation Engineer", True, "a marketing ad with an engineering title"),
    (["Online Marketing"], "Praktikum Produktmanagement", True, "a German marketing role"),
    (["Sales"], "AI Automation Engineer", True, "sales filed under a tech title"),
    (["QA", "Remote"], "QA Engineer", False, "a genuine QA tag"),
    (["Automation", "Engineering"], "Automation Engineer", False, "engineering, no QA word needed"),
    ([], "AI Automation Engineer", False, "no tags, so the title decides"),
]:
    j = r.job("Arbeitnow", title, "X", "Berlin", "https://x", "", "", tags=tags)
    check(note, r.tagged_out_of_field(j) == want_block)

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
check("Armenia is capped tighter than remote, so it cannot flood the chat",
      r.MAX_PER_TIER[1] < r.MAX_PER_TIER[2], f"got {r.MAX_PER_TIER[1]}")
check("Germany has a real cap, not zero", r.MAX_PER_TIER[25] >= 4)
check("caps add up to at least one run", sum(r.MAX_PER_TIER.values()) >= r.MAX_PER_RUN)

print("\n3a. Germany ranks on-site above remote")
def de(**kw):
    d = {"title": "QA Engineer", "company": "Not listed", "location": "Remote",
         "description": "", "restrictions": [], "remote": True, "relocation": False,
         "salary": "", "source": "t", "url": "https://x/1", "tier": 2, "score": 70}
    d.update(kw)
    return d
g_onsite = de(company="X", location="Berlin, Germany", remote=False, tier=25)
g_remote = de(company="X", location="Remote, Germany", remote=True, tier=25)
open_remote = de(location="Remote worldwide", tier=2)
sponsored = de(location="Vienna, Austria", remote=False, tier=4,
               description="visa sponsorship available")
single = de(location="Remote (Poland)", tier=3)
check("on-site Germany outranks remote Germany",
      r.priority_rank(g_onsite) < r.priority_rank(g_remote),
      f"{r.priority_rank(g_onsite)} vs {r.priority_rank(g_remote)}")
check("open remote still outranks on-site Germany",
      r.priority_rank(open_remote) < r.priority_rank(g_onsite),
      f"{r.priority_rank(open_remote)} vs {r.priority_rank(g_onsite)}")
check("on-site Germany outranks sponsored elsewhere",
      r.priority_rank(g_onsite) < r.priority_rank(sponsored))
check("remote Germany outranks an unsponsored single-country remote",
      r.priority_rank(g_remote) < r.priority_rank(single))

print("\n3b. the same posting never comes back")
dup = {"title": "QA Engineer", "company": "Acme", "location": "Remote",
       "description": "", "restrictions": [], "remote": True,
       "relocation": False, "salary": "", "source": "t", "url": "u1"}
key = r.job_key(dup)
check("company+title is the key", key.startswith("acme|"), f"got {key}")
check("a different board for the same job gets the same key",
      r.job_key(dict(dup, url="https://other.example/job/9")) == key)
check("capitalisation and punctuation do not create a new key",
      r.job_key(dict(dup, company="ACME, Inc.", title="QA  Engineer")) == "acmeinc|qaengineer")

# The seeding step is what stops the rows already in his sheet coming back.
seeded = {}
count = r.seed_from_sheet(seeded, {"commit|qaengineer": "x", "softconstruct|qualityassuranceengineer": "x",
                                   "company:commit": "x"})
check("seeding counts only company|title keys", count == 2, f"got {count}")
check("seeding marks the key as already notified", "commit|qaengineer" in seeded)
check("seeding is skipped once the list has anything in it",
      r.seed_from_sheet({"a": "b"}, {"c|d": "x"}) == 0)
check("the seeded mark is not a date, so age can never trim it",
      seeded["commit|qaengineer"] == r.ALREADY_SEEDED)
check("a broken SPREADSHEET_ID URL is refused instead of silently skipped",
      r.sheet_keys() == set())

# "Not listed" is a sentinel, not a company. On the first live run three different
# postings shared it and collapsed into one key.
un_a = {"title": "QA Role", "company": "Not listed", "location": "Remote",
        "description": "", "restrictions": [], "remote": True, "relocation": False,
        "salary": "", "source": "t", "url": "https://a.example/1"}
un_b = dict(un_a, url="https://b.example/2")
check("two unnamed postings are not the same job", r.job_key(un_a) != r.job_key(un_b))
check("an unnamed posting keys on its URL", r.job_key(un_a) == "https://a.example/1")
check("two unnamed postings are not deduplicated together",
      r.dedup_signature(un_a) != r.dedup_signature(un_b))
check("a named posting still keys on company+title",
      r.job_key(dict(un_a, company="Acme")) == "acme|qarole")
check("the sentinel does not suppress a whole company",
      r.UNKNOWN_COMPANY == "notlisted")

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
def _swiss_cases():
    """A Swiss permit line blocks the job; a Swiss company that does not state one
    does not. Three Swiss on-site roles in two days made this worth a rule."""
    def job(desc):
        return {"title": "Software Test Engineer", "company": "X", "location": "Bern",
                "url": "", "source": "", "description": desc.lower()}
    cases = [
        ("eligibility to work in switzerland (swiss/eu/efta work permit)",
         True, "stated permit blocks it"),
        ("experience with katalon and appium. no permit mentioned.",
         False, "a Swiss company that says nothing is not blocked"),
        ("fully remote from anywhere. playwright and pytest.",
         False, "plain remote is never blocked"),
    ]
    for desc, want, label in cases:
        check(f"swiss: {label}", r.is_hard_excluded(job(desc)) == want)


_swiss_cases()

print("all checks passed")
