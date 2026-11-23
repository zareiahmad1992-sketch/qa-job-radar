# 📤 هر ۵ فایل — این‌جا، همین پوشه

**هیچ‌جا نرو. همه‌شان در `UPLOAD_NOW/` هستند و با یک لینک پایین صفحه دانلود می‌شوند.**

---

# ۱️⃣ فایل‌ها — یکی‌یکی، به همین ترتیب

| # | فایل (بزن روش) | کجا برود در مخزن |
|---|---|---|
| ۱ | `UPLOAD_NOW/hourly_watch.py` | **ریشهٔ مخزن** |
| ۲ | `UPLOAD_NOW/seen_hourly.json` | **ریشهٔ مخزن** |
| ۳ | `UPLOAD_NOW/email_alerts.py` | **ریشهٔ مخزن** (جایگزین) |
| ۴ | `UPLOAD_NOW/email_replies.py` | **ریشهٔ مخزن** (جایگزین) |
| ۵ | `UPLOAD_NOW/workflows/hourly_watch.yml` | **`.github/workflows/`** |

## 🎁 یا همه با هم

`UPLOAD_NOW.zip` — ۲۲ کیلوبایت، شامل هر ۵ فایل با ساختار درست.

---

# ۲️⃣ اگر اولی را فرستادی، بقیه را چطور؟

**روش گوشی (وب گیت‌هاب):**

```
گام ۱   App Files / Files را باز کن → UPLOAD_NOW
گام ۲   hourly_watch.py را باز کن → دکمهٔ Share → Copy
گام ۳   گیت‌هاب → مخزن → Add file → Create new file
گام ۴   نام: hourly_watch.py
گام ۵   محتوا را Paste کن → Commit changes
```

⚠️ **فایل `.py` را نمی‌شود از گوشی «آپلود» کرد** — گیت‌هاب وب در Safari فقط ادیتور متن می‌دهد. پس Copy/Paste تنها راه است. `seen_hourly.json` همان سه کاراکتر است: `[ ]`

**اگر کامپیوتر داری** (بعداً):
```bash
cd مسیر/مخزن
cp ~/Downloads/hourly_watch.py ~/Downloads/seen_hourly.json .
cp ~/Downloads/email_alerts.py ~/Downloads/email_replies.py .
cp ~/Downloads/hourly_watch.yml .github/workflows/
git add -A && git commit -m "Hourly mail watch + fix email search and ATS filter bugs" && git push
```

---

# ۳️⃣ بعد از آپلود — تست

```
GitHub → Actions → Hourly Mail Watch → Run workflow → dry_run = true
```

**دنبال این خط در لاگ بگرد:**

```
3 event(s), 0 security item(s)
  [Applied   ] TeleClinic      Deine Bewerbung als Quality Assurance Specialist
  [Applied   ] Oppizi          Thanks for applying to Oppizi
sheet: Jobs row 68 TeleClinic Applied -> Applied
```

اگر این را دید، **کار می‌کند**. لاگ را بفرست، خودم می‌خوانم.

---

# ❗ تنها چیزی که ممکن است لازم شود

**Secretها همه از قبل هستند** — `EMAIL_USER` · `EMAIL_APP_PASSWORD` · `SPREADSHEET_ID` · `GOOGLE_SERVICE_ACCOUNT_JSON` · `TELEGRAM_BOT_TOKEN` · `TELEGRAM_CHAT_ID`

**اگر یکی نبود**، اسکریپت **بلند خطا می‌دهد** و در تلگرام می‌نویسد کدام. ساکت نمی‌ماند.

```
EMAIL_USER and EMAIL_APP_PASSWORD are not set.
```
این را دیدی → `Settings → Secrets → Actions` → آن یکی را اضافه کن.

---

# 🧠 خلاصهٔ ۳۰ ثانیه‌ای

```
hourly_watch.py        جدید  — هر ساعت ایمیل می‌خواند، شیت را می‌نویسد
seen_hourly.json       جدید  — حافظه‌اش (محتوایش: [])
hourly_watch.yml       جدید  — کرون هر ساعت
email_alerts.py        رفع باگ — سرچ ایمیل همیشه خالی برمی‌گشت
email_replies.py       رفع باگ — فقط LinkedIn را می‌دید، ATSها فیلتر می‌شدند
```


---

# 🆕 نسخهٔ جدید — ۲ فایل دیگر هم عوض شد

**تست را نوشتم و اجرا کردم: `ALL TESTS PASS`**

و در همان تست **یک باگ بد** پیدا شد:

```
قبل:  ایمیل Digitain دربارهٔ QA  →  نوشته شد روی ردیف «Georgian Game Presenter» ❌
بعد:  ایمیل Digitain دربارهٔ QA  →  ردیف «Senior QA Engineer | Manual» ✅
```

**دلیل:** کد فقط نام شرکت را می‌دید، عنوان شغل را نه. Digitain سه اپلای هم‌زمان دارد و ردیف آخر را می‌گرفت.

**الآن عنوان شغل هم مطابقت داده می‌شود:**

| ایمیل می‌گوید | ردیف درست |
|---|---|
| Senior QA Engineer \| Manual | Senior QA Engineer \| Manual ✅ |
| Persian Speaking Game Presenter | Persian Game Presenter ✅ |
| Georgian Speaking Game Presenter | Georgian Game Presenter ✅ |
| عنوانی ندارد | ردیف آخر (حدس) 🟡 |

---

## 📤 فایل‌ها — همه در `UPLOAD_NOW/`

| فایل | کجا |
|---|---|
| `hourly_watch.py` | ریشهٔ مخزن — **عوض شد** |
| `test_hourly_watch.py` | ریشهٔ مخزن — **جدید، اختیاری** |
| `seen_hourly.json` | ریشهٔ مخزن |
| `email_alerts.py` | ریشهٔ مخزن — رفع باگ |
| `email_replies.py` | ریشهٔ مخزن — رفع باگ |
| `workflows/hourly_watch.yml` | `.github/workflows/` |

⚠️ **اگر `hourly_watch.py` را قبلاً آپلود کردی، دوباره آپلود کن.** نسخهٔ قبلی باگ عنوان شغل داشت.

---

## ✅ تست بدون هیچ شبکه‌ای

اگر خواستی خودت هم ببینی:

```
python3 test_hourly_watch.py
```

```
ALL PASS
  [PASS] MSD closed by a rejection
  [PASS] ImagineLive row not touched by the Digitain mail
  [PASS] no noise events
  [PASS] security mail pushed to Telegram
  [PASS] one Telegram message
  ...
```

نه ایمیل، نه شیت، نه تلگرام لازم دارد. همه‌شان شبیه‌سازی شده‌اند.


---

# 🔴 لاگ scan-and-send — ۴ باگ واقعی دیگر

| نشانه در لاگ | علت | اثر |
|---|---|---|
| `Sheet duplicate check skipped: KeyError: 'GOOGLE_CLIENT_ID'` | هیچ credential گوگل در Actions نیست | **گارد تکراری خاموش** |
| `Sheet sync skipped: no Google credentials` | همان | **شیت از اجرای رادار پر نمی‌شود** |
| `Staff.am: skipped (HTTP Error 403)` | staff.am ربات را بلاک می‌کند | **بهترین منبع ارمنستان خاموش** |
| `fit check failed (ModuleNotFoundError)` ×۵ | `import pymupdf` که نصب نیست | **هر کاورلتر ساده‌ترین نسخه** |
| `Email alerts: 0 fetched` | هم‌ارز باگ `newerr_than` | ایمیل خوانده نمی‌شود |

## ✅ رفع شد

**۱. `tailor.py` — شمارش صفحه بدون pymupdf**
```python
number of /Type /Page objects   →  تست شد: ۱، ۲، ۳ صفحه درست شمرد
```
دیگر `ModuleNotFoundError` ندارد. کاورلتر با richest نسخه ساخته می‌شود.

**۲. `job_radar.py` + `job_sheet_sync.py` — پیام خطای گوگل**
```
قبل:  KeyError: 'GOOGLE_CLIENT_ID'          ← هیچ نمی‌گوید
بعد:  No usable Google credentials. Either set GOOGLE_SERVICE_ACCOUNT_JSON
      (recommended) or all of GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and
      GOOGLE_REFRESH_TOKEN. Missing: GOOGLE_CLIENT_ID, ...
```

**۳. `job_radar.py` — staff.am**
هدرهای مرورگر + یک retry با ۵ ثانیه مکث. اگر باز ۴۰۳ داد، **صریح می‌گوید** منبع ارمنستان خاموش است، نه اینکه ساکت صفر بدهد.

---

# ⚠️ کاری که فقط تو می‌توانی بکنی — ۲ دقیقه

```
GitHub → Settings → Secrets and variables → Actions
```

**ببین کدام‌ها هستند:**

| Secret | لازم؟ |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ **این را بساز** |
| `SPREADSHEET_ID` | ✅ باید باشد |
| `EMAIL_USER` | ✅ برای hourly_watch |
| `EMAIL_APP_PASSWORD` | ✅ برای hourly_watch |
| `TELEGRAM_BOT_TOKEN` | ✅ |
| `TELEGRAM_CHAT_ID` | ✅ |

**اگر `GOOGLE_SERVICE_ACCOUNT_JSON` نیست:**
```
۱  console.cloud.google.com → IAM → Service Accounts → Create
۲  Keys → Add key → JSON → دانلود
۳  محتوای کل فایل را در Secret بگذار
۴  ایمیل سرویس‌اکانت را در Google Sheets → Share کن (Editor)
```

**بدون این، شیت از سمت رادار هرگز پر نمی‌شود** — حتی اگر hourly_watch بی‌نقص باشد.

---

# 📤 فایل‌هایی که باید آپلود شوند

| فایل | کجا | چرا |
|---|---|---|
| `hourly_watch.py` | ریشه | جدید |
| `seen_hourly.json` | ریشه | `[ ]` |
| `workflows/hourly_watch.yml` | `.github/workflows/` | کرون |
| `job_radar.py` | ریشه | staff.am + پیام خطا |
| `job_sheet_sync.py` | ریشه | پیام خطا |
| `tailor.py` | ریشه | **رفع کاورلتر** |
| `email_alerts.py` | ریشه | رفع سرچ |
| `email_replies.py` | ریشه | رفع فیلتر ATS |
| `test_hourly_watch.py` | ریشه | اختیاری |
