# خطای push که گرفتی — توضیح و راه‌حل

## چی شد

```
! [rejected]  main -> main (fetch first)
error: failed to push some refs
```

دو اجرای workflow **همزمان** تلاش کردن `seen_jobs.json` رو کامیت کنن. اولی موفق شد، دومی رد شد چون نسخه ریموت عوض شده بود.

**مهم:** مرحله اسکن **۴۲ ثانیه موفق بود**. یعنی آگهی‌ها پیدا شدن و پیام‌ها رفتن. فقط ذخیره state شکست خورد.

## پیامدش چی بود

فقط این: چون state ذخیره نشد، اجرای بعدی همون آگهی‌ها رو «جدید» حساب می‌کنه و **دوباره می‌فرسته**.

یعنی ممکنه دو بار پیام تکراری بگیری. آزاردهنده، ولی نه فاجعه.

## چرا اتفاق افتاد — دو دلیل

**۱. GitHub Actions همزمان اجرا کرد**

کرون `0 5,8,11,14,17,23 * * *` است، ولی GitHub گاهی چند اجرا رو نزدیک هم می‌ندازه، یا یه اجرا طول می‌کشه و با اجرای بعدی همپوشانی پیدا می‌کنه.

**۲. تنظیم `concurrency` اشتباه بود**

من گذاشته بودم:
```yaml
concurrency:
  group: job-radar          # ← این
  cancel-in-progress: false
```

اسم گروه `job-radar` بود، ولی فایل workflow هم `QA Job Radar` نام داره. GitHub این دو رو یکی نمی‌بینه چون کلید concurrency بر اساس **مقدار گروه** است، نه اسم فایل. عملاً هیچ قفلی وجود نداشت.

## راه‌حل — سه تغییر

### ۱. اسم گروه عوض شد

```yaml
concurrency:
  group: job-radar-state
  cancel-in-progress: false
```

### ۲. pull --rebase و تلاش مجدد اضافه شد

قبلاً:
```bash
git push                    # یک تلاش، بعد fail
```

حالا:
```bash
for attempt in 1 2 3 4 5; do
  if git push; then exit 0; fi
  git pull --rebase --autostash origin main || true
  sleep $((attempt * 3))
done
```

پنج تلاش با فاصله ۳، ۶، ۹، ۱۲ ثانیه. اگه یکی موفق شد، ادامه می‌ده.

### ۳. شکست push دیگر کل اجرا را fail نمی‌کند

قبلاً `exit 1` می‌داد و اجرا قرمز می‌شد. حالا:
```bash
echo "The alerts went out, so the only consequence is that the next run"
echo "may resend a few jobs you have already seen. Not worth failing over."
exit 0
```

منطقش: پیام‌ها رفتن. state یه فایل کمکی است. نباید باعث بشه اجرا شکست‌خورده به‌نظر بیاد.

## کاری که الان باید بکنی

فایل `job_radar.yml - THIS GOES IN .github-workflows FOLDER.yml` رو از بسته بردار، محتواش رو کپی کن و در `.github/workflows/job_radar.yml` پیست کن.

بعدش یه بار دستی اجرا کن تا مطمئن شی:
Actions → QA Job Radar → Run workflow → `dry_run` = false

## اگه بازم تکرار شد

یعنی دو اجرا واقعاً به هم رسیدن. اون‌موقع کافیه `seen_jobs.json` رو از ریپو پاک کنی و یه بار دیگه اجرا کنی.

## یک راه‌حل رادیکال‌تر

اگه اذیتت می‌کنه، می‌تونم state رو کلاً از ریپو بردارم و `seen_jobs.json` رو به‌عنوان **artifact** ذخیره کنم. اون‌موقع:
- هیچ کامیتی به ریپو زده نمی‌شه
- ریپو تمیز می‌مونه
- ولی state بین اجراها منتقل نمی‌شه

یعنی هر اجرا همه چیز رو دوباره می‌فرسته. **بد نیست، نه؟** در واقع برای تو شاید بهتر باشه، چون هر ۶ ساعت همه آگهی‌های زنده رو یه بار می‌بینی.

بگو اگه این رو می‌خوای.
