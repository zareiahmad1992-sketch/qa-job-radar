# QA Job Radar

Scans public job feeds and sends software QA roles to Telegram, ranked by how
usable they are for someone living in Yerevan, Armenia.

No LinkedIn, no scraping behind a login, no API keys. Every source below is a
public feed or a public page.

## Priority order

Four tiers, best first. The order is by how little has to happen before Ahmad can
say yes, not by how interesting the job sounds.

| Tier | What it means | What has to happen first |
|---|---|---|
| 🇦🇲 **1 — Armenia** | On-site or remote, any employer in Armenia | Nothing. He lives there. |
| 🌍 **2 — Remote, open** | Worldwide, anywhere, EMEA, Europe, or several countries named | A contract. No permit needed. |
| 🌍 **3 — Remote, one country** | "Remote (Poland)", "Remote (Spain)", "Remote (Portugal)" | He would need to be resident there, or they accept a contractor anyway |
| 🛂 **4 — On-site Europe + sponsorship** | Office in Europe, and the posting offers a visa or relocation | A visa process, months |

Anything outside those four is dropped.

Tier 3 carries a warning line in the message, because "remote" on a Polish posting
usually means "remote, from your flat in Poland". Tier 4 carries a different warning,
because sponsorship is real but slow.

### Why tier 1 outranks the remote tiers

Armenia is the only place where there is nothing between him and the job. No permit,
no relocation, no timezone overlap to negotiate, no question about the contract type.
A Yerevan on-site role at 55% skill match is more useful than a remote role at 90%
that turns out to require residency in Poland. The score is still shown, but the tier
decides the order.

## Sources

| Source | Why it is here | Jobs per scan |
|---|---|---|
| **LinkedIn** | The guest endpoint needs no login and no API key. This is by far the best Armenia source: 75 unique Yerevan QA postings against Staff.am's 5, including employers that never use Staff.am | ~130 |
| **Arbeitnow** | Germany-heavy, includes a per-job remote flag | ~600 |
| **Landing.jobs** | Europe-focused, and has a real `relocation_paid` flag | ~150 |
| **Remote OK** | Large remote board | ~97 |
| **Himalayas** | Publishes per-job location restrictions, the cleanest "can I actually take this" signal | ~60 |
| **Jobicy** | Remote board with a geography field | ~50 |
| **Djinni** | Eastern European market, publishes schema.org JSON-LD | ~45 |
| **Working Nomads** | Remote board with a location field | ~34 |
| **We Work Remotely** | RSS feed that includes a region per posting | ~25 |
| **Remotive** | Software-only remote board | ~16 |
| **Staff.am** | Armenia on-site and remote | ~10 |

Roughly 1,200 postings are scanned per run. About 92 pass the filters, and the per-tier
caps send about 15 to Telegram.

### About the LinkedIn source

Two things worth knowing.

**It needs `Yerevan, Armenia`, not `Armenia`.** Searching for the country name resolves
to Armenia, Colombia, and returns Spanish-language QA jobs in Pereira. That cost an
hour to notice.

**Remote jobs do not always say "remote" in the location field.** A posting filtered
with LinkedIn's `f_WT=2` (remote only) still shows `Katowice, Śląskie, Poland` as its
location. The parser marks those as remote explicitly, otherwise every Polish remote
role reads as on-site and gets dropped.

**Descriptions are not available.** LinkedIn does not serve the job body to a guest, so
those postings are scored on the title alone and labelled `(title only)`. That is
honest about how much evidence there is.

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
French or Spanish are dropped, as are titles written in a foreign language. So are
postings that require a native speaker of Ukrainian, Russian or Polish. A job that
merely mentions a German client is not dropped.

**Blocked by country lock.** If a posting says it will only hire in one named
country, and that country is Armenia's not, it is dropped. Two-letter codes count
too, so "Remote (UA)" is caught alongside "Ukraine-based role". Phrases like "Ukraine-based
role", "countries we consider candidates", "only consider candidates", "must be
located in" and "US work authorization" all trigger this.

Djinni hides both of those rules in the rendered page rather than in its clean
JSON-LD block, so Djinni jobs that survive every other filter get one extra request
to read the "Required languages" and "Countries we consider candidates" sections.
That costs about nine requests per run and it is the check that keeps Ukrainian-only
postings out of the chat. Before it existed, four such postings arrived with a 97%
fit score.

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

## The Google Sheet tracker

`job_sheet_sync.py` pushes radar results into a Google Sheet so the tracker lives
in one place instead of scrolling a Telegram chat.

Columns: date found, tier, company, job title, location, fit %, source, salary,
status, applied on, link, notes.

You fill in two of them. Everything else arrives by itself.

**Status** is a dropdown: Not applied, Application started, Applied, Interview,
Rejected, Skip, Offer. Change it as things move.

**Applied on** is the date you actually sent it. Useful three weeks later when you
are wondering who has gone quiet.

The **Summary** tab counts everything by tier and status with formulas, so it
updates the moment you change a row.

### Setup, once

1. Google Cloud console, new project, enable the Google Sheets API
2. IAM, create a service account
3. That service account, Keys, add a JSON key, download it
4. Open the sheet, Share, paste the service account email, give it Editor
5. GitHub repo, Settings, Secrets, add:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — the whole JSON file, pasted as one line
   - `SPREADSHEET_ID` — the id from the sheet URL

Until those exist the sync step prints a message and skips, so nothing breaks.

### How the two workflows fit together

`scan-and-send` pushes new jobs to Telegram.
`sync-to-sheet` dumps the same run to JSON and appends anything the sheet does not
already have.

Both run on the same cron. They are separate jobs so a Telegram outage never stops
the sheet from updating, and a Sheets quota error never stops your alerts.

## What I deliberately did not build

**Reading Telegram reactions.** The Telegram Bot API does not deliver reaction
updates for messages in a private chat between you and your bot; that requires the
bot to be an administrator in a channel. On top of that, GitHub Actions has no
listener, so there is nothing to receive a webhook. Every design I could think of
for "I like it in Telegram, it appears in the sheet" needed a server running all
the time, which is a much bigger thing than this cron job.

The sheet solves the same problem from the other end. The radar writes every job
into it, you set the status, and nothing depends on reading a reaction back.

**Reading Gmail from the cron job.** Possible with an OAuth refresh token, but the
job alerts Gmail sends are mostly the same postings the radar already finds, plus
a lot of marketing. If you want it, the path is a Google Cloud OAuth client and a
`GMAIL_REFRESH_TOKEN` secret, and I would add a parser per alert sender (LinkedIn,
Teal, Pracuj) because they all format differently.
