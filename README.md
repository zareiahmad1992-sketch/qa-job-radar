# QA Job Radar

Scans public job feeds and sends software QA roles to Telegram, ranked by how
usable they are for someone living in Yerevan, Armenia.

No LinkedIn, no scraping behind a login, no API keys. Every source below is a
public feed or a public page.

## Priority order

Jobs are sent in three tiers, best first.

| Tier | What it means |
|---|---|
| 🥇 | Remote, and open to Europe / EMEA / worldwide / Armenia. No relocation needed. |
| 🥈 | Armenia. On-site or remote, any employer. Already where you are. |
| 🥉 | On-site in Europe, but only when the posting mentions visa sponsorship or paid relocation. |

Anything that does not land in one of those three tiers is dropped.

## Sources

| Source | Why it is here | Jobs per scan |
|---|---|---|
| **Himalayas** | Publishes per-job location restrictions, which is the cleanest "can I actually take this" signal | ~60 |
| **Djinni** | Eastern European market, publishes schema.org JSON-LD so parsing is exact | ~44 |
| **Remotive** | Software-only remote board | ~14 |
| **Jobicy** | Remote board with a geography field | ~50 |
| **Working Nomads** | Remote board with a location field | ~35 |
| **We Work Remotely** | RSS feed that includes a region per posting | ~25 |
| **Remote OK** | Large remote board | ~98 |
| **Landing.jobs** | Europe-focused, and has a real `relocation_paid` flag | ~150 |
| **Arbeitnow** | Germany-heavy, includes a per-job remote flag | ~600 |
| **Staff.am** | Armenia on-site and remote | ~9 |

Roughly 1,000 postings are scanned per run. About 25 survive the filters.

## What gets filtered out

This is the part that matters. A search for "quality engineer" mostly returns
manufacturing jobs, and those are a different profession.

**Blocked by title.** The job title has to name software QA work: QA, tester,
test engineer, SDET, test automation, quality assurance, and so on. A warehouse
job whose description happens to contain the word "test" does not get through.

**Blocked by industry.** Manufacturing and hardware quality roles are removed by
keyword: PFMEA, SPC, MSA, GR&R, Cp/Cpk, 8D, CAPA, ISO 13485, ISO 14971, MDSAP,
IATF, TS16949, calibration, injection molding, EMC, IEC, CE marking, regulatory
affairs, medical device, GMP, FDA. So are defence roles that need security
clearance, and postings that require US work authorization.

**Blocked by language.** Postings that demand fluent German, Dutch, Estonian,
French or Spanish are dropped, as are titles written in a foreign language. A job
that merely mentions a German client is not dropped.

**Blocked as a scam.** Known fee-charging and training-selling companies are on a
list, along with phrases like "paid training", "placement fee", "security
deposit" and "pay to start".

## Ranking inside a tier

Each job gets a fit score from 20 to 97.

- A point for every QA skill found in the text (Playwright, Selenium, Postman,
  API testing, SQL, Jira, TestRail, ISTQB, regression, exploratory, CI/CD and so on)
- A bonus for the tools that actually appear in Ahmad's CV
- A bonus for postings that state years of experience
- An adjustment for seniority: trainee, intern and junior postings score 18 lower,
  principal and architect postings 8 lower, because five years of experience sits
  between those two ends

## Location honesty

When a posting restricts hiring to one country, the message says so instead of
pretending it is worldwide:

> Check the location rule: this one names a country. Some of these hire across
> Eastern Europe, some do not.

That warning shows up on any job whose stated restriction names a country and does
not say worldwide, anywhere, global, EMEA or Europe.

## Running it

```bash
# Send new jobs to Telegram
python job_radar.py --once

# See what would be sent, without sending
python job_radar.py --dry-run

# See everything that passed the filters, grouped by tier
python job_radar.py --report
```

`--report` is the one to use when tuning the filters. It shows what got through
without touching Telegram or the seen-jobs file.

## Setup

Add two GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow runs every 15 minutes. `seen_jobs.json` keeps a rolling list of the
last 3,000 job URLs that were already sent, so nothing repeats.

At most 8 jobs go out per run. If more than that match, the rest wait for the next
scan, which keeps a backlog from flooding the chat.

## Files

```
job_radar.py                    the scanner
seen_jobs.json                  rolling list of already-sent jobs
.github/workflows/job_radar.yml the cron job
```
