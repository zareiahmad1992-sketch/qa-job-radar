# راه‌اندازی، مرحله به مرحله

این فایل را یک بار بخوان. بعد از آن رادار خودش کار می‌کند.

---

## ۱. آپلود روی گیت‌هاب

محتویات این پوشه را در ریپوی `qa-job-radar` بگذار. ساختار باید دقیقاً این باشد:

```
qa-job-radar/
├── .github/
│   └── workflows/
│       ├── job_radar.yml        سرچ، هر ۲ ساعت
│       └── reply_watch.yml      پایش ایمیل، هر ۱ ساعت
├── fonts/
│   ├── Carlito-Regular.ttf
│   ├── Carlito-Bold.ttf
│   ├── Carlito-Italic.ttf
│   └── Carlito-BoldItalic.ttf
├── job_radar.py                 موتور اصلی
├── job_sheet_sync.py            نوشتن در گوگل شیت
├── email_alerts.py              خواندن آگهی از ایمیل
├── email_replies.py             خواندن پاسخ مصاحبه
├── telegram_channels.py         خواندن کانال‌های تلگرام
├── tailor.py                    ساخت رزومه و کاورلتر PDF
├── test_radar.py                تست خودکار
├── channels.json                لیست کانال‌های تلگرام
├── seen_jobs.json               حافظه آگهی‌های دیده‌شده
├── seen_replies.json            حافظه پاسخ‌های دیده‌شده
├── REJECT_FAST.md               فیلتر رد سریع آگهی
└── README.md
```

**نکته مهم:** پوشه `.github` با نقطه شروع می‌شود. اگر در گیت‌هاب با دکمه
«Add file → Upload files» آپلود می‌کنی، مرورگر پوشه‌های مخفی را نمی‌پذیرد.
دو راه:

**راه اول، ساده‌تر:** فایل‌ها را با drag and drop آپلود کن، بعد برای هر فایل
workflow جداگانه از دکمه «Add file → Create new file» استفاده کن و اسمش را
دقیقاً بگذار `.github/workflows/job_radar.yml`. گیت‌هاب اسلش را خودش پوشه
می‌فهمد.

**راه دوم:** با git در ترمینال:

```bash
git clone https://github.com/zareiahmad1992-sketch/qa-job-radar.git
cd qa-job-radar
# محتویات این پوشه را کپی کن، بعد:
git add .
git commit -m "Job radar v10"
git push
```

**فایل قدیمی را پاک کن:** در روت ریپو یک فایل هست به نام
`job_radar.yml - THIS GOES IN .github-workflows FOLDER.yml`. باید حذف شود،
وگرنه با فایل جدید تداخل پیدا می‌کند.

---

## ۲. سکرت‌ها

Settings → Secrets and variables → Actions

### تب Secrets

| نام | مقدار | لازم برای |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | از @BotFather | همه چیز |
| `TELEGRAM_CHAT_ID` | `121203619` | همه چیز |
| `SPREADSHEET_ID` | از آدرس شیت، بخش بین `/d/` و `/edit` | آپدیت شیت |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | کل فایل JSON | آپدیت شیت |
| `EMAIL_USER` | آدرس جیمیل خودت | آگهی و پاسخ از ایمیل |
| `EMAIL_APP_PASSWORD` | ۱۶ کاراکتر | آگهی و پاسخ از ایمیل |

دو تای اول از قبل هست. چهار تای بعدی مانده.

### تب Variables

| نام | مقدار |
|---|---|
| `TELEGRAM_CHANNELS` | `qa_testirovshchik_jobs_vakansii, iJobAm_IT, iJobAm, get_joboffer, RemoRoute, job_labdon, jobs_finding, remotejobss, germantechjobs, Jobs_global_startups` |

---

## ۳. App Password گوگل

`EMAIL_APP_PASSWORD` رمز جیمیل نیست. باید این کار را بکنی:

1. `myaccount.google.com/security`
2. 2-Step Verification را روشن کن، اگر روشن نیست
3. پایین همان صفحه، App passwords
4. یک اسم بده مثل `qa-job-radar` و Create بزن
5. ۱۶ کاراکتر می‌دهد، با فاصله. فاصله‌ها را بردار و در سکرت بگذار.

بدون این، پایش پاسخ و خواندن ایمیل کار نمی‌کند، ولی بقیه رادار کار می‌کند.

---

## ۴. Service Account گوگل شیت

1. `console.cloud.google.com` → پروژه جدید
2. APIs & Services → Enable APIs → **Google Sheets API** را فعال کن
3. IAM & Admin → Service Accounts → Create Service Account
4. روی اکانت ساخته‌شده → Keys → Add Key → Create new key → **JSON**
5. فایل دانلود می‌شود. کل محتوایش را در سکرت `GOOGLE_SERVICE_ACCOUNT_JSON` بگذار
6. در فایل، فیلد `client_email` هست، چیزی شبیه
   `something@project.iam.gserviceaccount.com`. آن را کپی کن و **شیت را با آن
   ایمیل به اشتراک بگذار** با دسترسی Editor

بدون مرحله ۶، شیت آپدیت نمی‌شود و تو در پیام تلگرام هشدار
`⚠️ Sheet NOT updated` می‌بینی.

---

## ۵. تست دستی

Actions → QA Job Radar → Run workflow → `dry_run` را `true` بگذار → Run.

خروجی باید چیزی شبیه این باشد:

```
LinkedIn rotation: slot 09, this run covers employers A (17 queries)
LinkedIn: 30 fetched
Telegram channels: 22 fetched
Himalayas: 60 fetched
Djinni: 43 fetched
...
Filter stats: {'total': 1200, ..., 'kept': 14}
19 jobs passed the filters
```

اگر این را دیدی، همه چیز درست است. بعد یک بار با `dry_run` خاموش اجرا کن
تا پیام واقعی در تلگرام بیاید.

---

## ۶. زمان‌بندی

| چه چیزی | هر چند وقت | cron |
|---|---|---|
| سرچ آگهی | هر ۲ ساعت | `0 */2 * * *` |
| پایش پاسخ ایمیل | هر ۱ ساعت | `0 * * * *` |

گیت‌هاب cron را همیشه دقیق اجرا نمی‌کند. در عمل بین ۲ تا ۴ ساعت فاصله
می‌افتد. این طبیعی است و دست ما نیست.

**درباره چرخش LinkedIn:** هر اجرا ۹ کوئری ارمنستان را همیشه می‌زند، و از ۹۱
کوئری بقیه یک بخش را. یعنی هر شرکت و هر کشور روزی دو بار بررسی می‌شود، نه هر
دو ساعت. دلیلش این است که LinkedIn بعد از حدود دوازده درخواست پشت سر هم خطای
۴۲۹ می‌دهد و اجرای صد کوئری‌ای نیمه‌کاره می‌ماند. منابع رایگان (تلگرام، ایمیل،
و ده فید دیگر) در هر اجرا کامل خوانده می‌شوند، پس آگهی تازه حداکثر دو ساعت
بعد دیده می‌شود.

اگر خواستی چرخش را خاموش کنی و هر بار همه را بزنی، در ریپو واریابل
`LINKEDIN_ROTATE` را `0` بگذار. ولی انتظار ۴۲۹ زیاد داشته باش.

---

## ۷. اگر خطا دیدی

| خطا | معنی | راه حل |
|---|---|---|
| `Sheet NOT updated: ...` | سکرت گوگل نیست یا شیت به اشتراک گذاشته نشده | مرحله ۴، مخصوصاً مرحله ۶ آن |
| `EMAIL_USER and EMAIL_APP_PASSWORD are not set` | App Password نیست | مرحله ۳ |
| `telegram/xxx: skipped` | کانال خصوصی است یا اسمش غلط | فقط کانال عمومی خوانده می‌شود |
| `LinkedIn 429, waiting` | عادی است، خودش صبر می‌کند | کاری نکن |
| `No module named reportlab` | نصب نشده | در workflow نوشته شده، اگر دیدی یعنی مرحله ۱ ناقص است |
| `! [rejected] main -> main (fetch first)` | دو اجرا همزمان کامیت کردند | فایل workflow قدیمی است. نسخه جدید سه بار retry می‌کند |
| `Node.js 20 is deprecated` | هشدار، نه خطا | در نسخه جدید checkout@v5 و setup-python@v6 گذاشته شده |
| `ubuntu-latest will migrate to Ubuntu 26` | اطلاع، نه خطا | در نسخه جدید روی `ubuntu-24.04` قفل شده تا غافلگیر نشویم |

---

## ۸. تست خودکار

اگر چیزی در فایل‌ها عوض کردی، این را اجرا کن:

```bash
python test_radar.py
```

۵۷ بررسی، بدون شبکه، بدون سکرت. اگر همه `ok` بود، چیزی نشکسته.
