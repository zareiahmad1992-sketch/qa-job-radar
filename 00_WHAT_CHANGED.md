# v9 — تلگرام و شیت در یک مسیر

## چه چیزی عوض شد

قبلاً دو job جدا تو workflow بود: یکی تلگرام، یکی شیت. **حالا یکی شده.**

ترتیب جدید در هر اجرا:

```
1. اسکن ۱۱۰۰+ آگهی
2. فیلتر (زبان، کشور، تولیدی، کلاهبرداری)
3. حذف تکرار
4. انتخاب ۱۸ آگهی برتر
5. ارسال به تلگرام  ← و همین‌ها ثبت می‌شن
6. نوشتن sent_jobs.json
7. آپدیت گوگل شیت با همون لیست
8. ذخیره seen_jobs.json
```

**نکته مهم:** شیت **دقیقاً همون آگهی‌هایی** رو می‌گیره که تو تلگرام دیدی، نه یه اسکن دوباره. قبلاً ممکن بود دو اسکن با هم فرق کنن.

## فایل جدید

`job_radar.py` بعد از ارسال، `sent_jobs.json` می‌سازه (بدون توضیحات، فقط فیلدهای لازم). بعد `job_sheet_sync.sync_file()` همون رو می‌خونه.

`sent_jobs.json` تو `.gitignore` هست — کامیت نمی‌شه.

## اگر Google credentials نباشه

```
Sheet sync skipped: no Google credentials in this run
```

تلگرام کارش رو می‌کنه، شیت رد می‌شه. **هیچ چیزی خراب نمی‌شه.**

## ستون‌های شیت

| # | ستون | کی پر می‌کنه |
|---|---|---|
| 1 | Date found | خودکار |
| 2 | Tier | خودکار |
| 3 | Company | خودکار |
| 4 | Job title | خودکار |
| 5 | Location | خودکار |
| 6 | Fit % | خودکار |
| 7 | Source | خودکار |
| 8 | Salary | تو |
| 9 | **Status** | **تو** ← دراپ‌داون |
| 10 | Applied on | تو |
| 11 | Link | خودکار |
| 12 | Notes | تو |

## کدهای Google (اگه هنوز تنظیم نکردی)

سه چیز لازمه:

**۱.** Google Cloud Console → پروژه → فعال‌سازی **Google Sheets API**

**۲.** IAM → Service Accounts → ساخت → Keys → JSON → دانلود

**۳.** همون ایمیل service account رو در شیت **Editor** کن

بعد در GitHub → Settings → Secrets:

```
GOOGLE_SERVICE_ACCOUNT_JSON  = کل محتوای فایل JSON
SPREADSHEET_ID               = 1BVep4tiLBATSuUhIU0CWDQVzKMZBoyqsUjSnyWAAM20
```

## نتیجه تست واقعی

```
آگهی اسکن‌شده : 1402
قبول          : 220
حذف تکراری    : 28
به تلگرام     : 21
به شیت        : 21  (همون‌ها)
```

## فایل‌های این نسخه

| فایل | تغییر |
|---|---|
| `job_radar.py` | ✏️ می‌نویسه `sent_jobs.json` + صدا زدن شیت |
| `job_sheet_sync.py` | ✏️ تابع `sync_file()` |
| `.github/workflows/job_radar.yml` | ✏️ یک job به‌جای دو |
| `.gitignore` | 🆕 |
| `tailor.py` | — |
| `README.md` | — |
| `fonts/` | ۴ فایل |
| `seen_jobs.json` | محتواش `[]` |

## چک‌لیست آپلود

- [ ] `job_radar.py`
- [ ] `job_sheet_sync.py`
- [ ] `tailor.py`
- [ ] `README.md`
- [ ] `.gitignore`
- [ ] `seen_jobs.json` (محتواش `[]`)
- [ ] پوشه `fonts/` با ۴ فایل
- [ ] `job_radar.yml - THIS...` → `.github/workflows/job_radar.yml`
- [ ] فایل `job_radar.yml - THIS...` رو از روت پاک کن اگه هست
