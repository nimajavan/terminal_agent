# راهنمای دپلوی خودکار و عملیات سرور

نسخهٔ ۳، فرمان‌های مستقل `lta deploy` و `lta ops` را اضافه می‌کند. این مسیر برای اجرا به مدل زبانی درخواست نمی‌فرستد؛ بنابراین خطای Gemini یا مدل‌های دیگر وسط دپلوی اثر ندارد و مدل انتخاب‌شده تغییر نمی‌کند.

## راه‌اندازی یک‌باره

دسترسی SSH با کلید، اثر انگشت تأییدشدهٔ سرور در `known_hosts`، دامنه و اطلاعات محرمانه باید یک‌بار آماده شوند. برای نصب اولیه از Ubuntu یا Debian استفاده کنید. پورت‌های ۸۰ و ۴۴۳ باید آزاد و از اینترنت در دسترس باشند. اگر سرور nginx یا پراکسی دیگری دارد، ابتدا معماری ورودی را مشخص کنید؛ ابزار آن سرویس‌ها را حذف نمی‌کند.

```bash
python -m pip install --upgrade .
lta ops server add production --host root@server.example.com \
  --identity ~/.ssh/deploy \
  --apps demo api \
  --domains demo.example.com api.example.com \
  --operations deploy rollback backup restore restart
lta ops server setup production --bootstrap --service
```

نام میزبان، دامنه‌ها و مسیر کلید نمونه‌اند و باید جایگزین شوند. دستور اول فقط پروفایل محلی می‌سازد؛ `server setup` سیاست دسترسی را روی سرور ثبت می‌کند. `--bootstrap` نصب وابستگی‌ها از مخزن رسمی Docker را مجاز می‌کند و به SSH با کاربر root نیاز دارد. `--service` کارگر دائمی systemd و پنل محلی را نصب می‌کند تا صف کار، پایش و backup بعد از راه‌اندازی مجدد سرور ادامه پیدا کنند. دسترسی Docker از نظر سیستم‌عامل بسیار قدرتمند است؛ سیاست ابزار جای جداسازی کاربران نامطمئن روی سرور را نمی‌گیرد.

## معرفی و انتشار پروژه

```bash
lta ops inspect ./my-project
lta ops init ./my-project --name demo --domain demo.example.com --output ./my-project/lta.json
lta deploy ./my-project --server production --dry-run
lta deploy ./my-project --server production --autonomous --wait
```

فایل `lta.json` را یک‌بار از نظر پورت، دستور شروع، تست و مسیر بررسی سلامت بازبینی کنید. برای monorepo می‌توان به `ops init` گزینهٔ `--context apps/api` داد. JSON بدون وابستگی اضافه پشتیبانی می‌شود؛ برای YAML روی کنترل‌کننده `pip install '.[yaml]'` و روی سرور `python3-yaml` لازم است. نصب خودکار سرور این بسته را نیز نصب می‌کند.

دریافت مستقیم Git هم پشتیبانی می‌شود؛ پروژه باید manifest داشته باشد:

```bash
lta deploy https://github.com/your-org/your-app.git \
  --ref main --server production --autonomous --wait
```

زنجیرهٔ اجرا شامل بررسی سرور، snapshot کد، آماده‌سازی وابستگی‌ها، build، تست در کانتینر مجزا، backup قبل از migration، اجرای نسخهٔ جدید، بررسی سلامت داخلی، انتقال ترافیک HTTPS و دورهٔ تثبیت است. شناسهٔ انتشار در پاسخ پراکسی بررسی می‌شود تا پاسخ نسخه‌ای دیگر با موفقیت اشتباه گرفته نشود. در صورت شکست انتشار، مسیر قبلی بازگردانده می‌شود. نسخهٔ قبلی برای rollback سریع حفظ می‌شود؛ مصرف RAM هر دو نسخه را در ظرفیت سرور حساب کنید.

پشتیبانی اولیه شامل Node/npm، Python، سایت استاتیک آماده، Dockerfile و Compose محدودشده است. برای زبان‌ها و فریم‌ورک‌های دیگر Dockerfile بدهید. برنامه باید روی `0.0.0.0` و پورت manifest گوش کند. فایل Compose ورودی نباید host mount، پورت عمومی، حالت privileged، فایل محیطی میزبان یا شبکهٔ خارجی داشته باشد. سرویس‌های پایدار و volumeها در manifest تعریف می‌شوند. مثال‌ها در `examples/deployment` قرار دارند؛ فایل Python یک الگوی تنظیمات است و پروژهٔ FastAPI کامل همراه آن نیست.

## اسرار، دیتابیس و فضای ذخیره‌سازی

```bash
lta ops secret api-secret --server production
lta ops secret api-database --server production --from-env API_DATABASE_PASSWORD
```

مقادیر از ورودی SSH منتقل و در فایل خصوصی با دسترسی 0600 ذخیره می‌شوند؛ در آرگومان دستور یا prompt مدل قرار نمی‌گیرند. این مخزن فایل خصوصی است، نه vault رمزنگاری‌شده. manifest فقط نام مرجع را نگه می‌دارد. تنظیمات کانتینر و Compose خصوصی برای اجرای برنامه حاوی مقدار واقعی خواهند بود.

با `database` یک PostgreSQL 16 و با `redis: true` یک Redis 7 پایدار روی شبکهٔ خصوصی برنامه ساخته می‌شود. برنامه متغیرهای `PGHOST`، `PGPORT`، `PGDATABASE`، `PGUSER`، `PGPASSWORD` و در صورت نیاز `REDIS_HOST` و `REDIS_PORT` دریافت می‌کند. تغییر صرف فایل رمز، رمز PostgreSQL موجود را عوض نمی‌کند؛ ابزار این اختلاف را تشخیص می‌دهد و متوقف می‌شود.

`volumes` نام volume را به مسیر داخل کانتینر در `/data/` متصل می‌کند. برای نمونه: `{"uploads":"/data/uploads"}`. هیچ Docker socket یا مسیر میزبان وارد کانتینر برنامه نمی‌شود.

## وضعیت، خرابی و بازیابی عملیات

```bash
lta ops jobs --server production
lta ops jobs JOB_ID --server production
lta ops logs demo --server production
lta ops diagnose demo --server production
lta ops releases demo --server production
lta ops rollback demo --server production
lta ops retry FAILED_JOB_ID --server production
```

قطع SSH یا لغو انتظار محلی، کار ثبت‌شده روی سرور را لغو نمی‌کند. قطع ناگهانی کارگر باعث تکرار کورکورانهٔ دستورهای تغییردهنده نمی‌شود؛ کار نامطمئن به `needs_attention` می‌رود و وضعیت مسیر ترافیک بررسی می‌شود.

migration باید با نسخهٔ قبلی برنامه سازگار باشد. اگر هنگام migration پردازش قطع شد، ابتدا وضعیت دیتابیس را بررسی و اصلاح کنید؛ سپس `lta ops retry JOB_ID --server production --migration-resolved` اجرای جایگزین را بدون تکرار migration آغاز می‌کند. این گزینه فقط پس از بررسی واقعی داده‌ها استفاده شود. rollback برنامه، دیتابیس را خودکار به گذشته برنمی‌گرداند.

## پشتیبان و بازگردانی

تا زمانی که کاری در وضعیت `needs_attention` باشد، تعمیر و backup خودکار آن برنامه متوقف می‌شود تا نویسنده‌های داده پس از restore ناموفق ناخواسته روشن نشوند. بعد از بررسی و اصلاح رخداد غیر از migration، با `lta ops resolve JOB_ID --server production --acknowledge` رفع ابهام را ثبت کنید؛ این دستور خودش داده‌ها را تعمیر نمی‌کند.

```bash
lta ops backup api --server production
lta ops jobs JOB_ID --server production
lta ops drill api BACKUP_ID --server production
lta ops export-backup api BACKUP_ID ./api.dump --server production
lta ops import-backup api ./api.dump --server replacement
lta ops restore api BACKUP_ID --server replacement --allow-data-restore
```

هر شناسه به یک backup دیتابیس یا volume مربوط است؛ تمام خروجی‌های یک نوبت را برای بازیابی هماهنگ نگه دارید. PostgreSQL با `pg_dump` پشتیبان گرفته می‌شود و drill واقعاً در دیتابیس موقت restore می‌کند. برای volumeها و Redis، برنامه‌های نویسنده موقتاً متوقف می‌شوند؛ این نوع backup یک وقفهٔ نگهداری دارد. بازیابی مخرب ابتدا backup ایمنی می‌گیرد. شکست restore داده‌ها می‌تواند برنامه را برای بررسی اپراتور متوقف نگه دارد.

خروجی export شامل فایل داده و فایل `.json` کنار آن است؛ هر دو را نگه دارید. backup محلی روی همان سرور در برابر نابودی آن سرور کافی نیست؛ باید export شود. روی سرور تازه، ابتدا پروفایل و سرویس‌ها، اسرار و همان نسخهٔ پروژه را آماده کنید، سپس backupها را import و restore کنید. بازیابی آزمایشی را با دامنه‌ای جداگانه انجام دهید و پس از سلامت‌سنجی DNS اصلی را منتقل کنید.

## پایش، DNS و انتشار مداوم

```bash
lta ops watch --server production
lta ops status --server production
lta ops dashboard --server production
```

پنل فقط روی loopback سرور فعال است و فرمان آخر تونل SSH می‌سازد. وضعیت برنامه‌ها و کارها نمایش داده می‌شود؛ API وضعیت، اطلاعات پایش را نیز برمی‌گرداند. مصرف منابع، سلامت، خطاهای OOM، فضای دیسک و اعتبار گواهی بررسی می‌شوند. تعمیر خودکار به restart مشخص محدود است، مجوز جداگانه دارد و تعداد تلاش آن برای هر انتشار محدود است. تغییرات مهم در `alerts.jsonl` ثبت می‌شوند؛ پیام خارجی خودکار ارسال نمی‌شود.

برای ساخت DNS خودکار، بلوک `dns` با provider برابر `cloudflare`، zone، نشانی عمومی IP و مرجع token تنظیم کنید و مجوز `dns` بدهید. ابزار فقط رکورد دامنهٔ مجاز را تغییر می‌دهد و تعارض رکوردها را رد می‌کند. جزئیات در [راهنمای کامل](DEPLOYMENT.md) است.

نمونهٔ GitHub Actions در `examples/deployment/github-actions.yml` قرار دارد؛ آن را در مخزن برنامه کپی و اسرار SSH، میزبان و commit تأییدشدهٔ LTA را تنظیم کنید. برای staging و production پروفایل و manifest جدا تعریف کنید.

تست Docker واقعی، یک job جدا در CI دارد. تست‌های محلی بدون دسترسی سرور، نمی‌توانند صدور گواهی عمومی، DNS و سیاست شبکهٔ محیط واقعی شما را تضمین کنند؛ اولین انتشار باید روی محیط آزمایشی همان زیرساخت بررسی شود.
