# 🎓 Tillar markazi — SMART EDUCATION CENTER

Til o'quv markazi uchun to'liq boshqaruv tizimi: **4 rol** (administrator, o'qituvchi, o'quvchi, ota-ona), davomat va Coin/XP,
uy vazifasi jarimalari, **1v1 Battle** (Elo reyting, ligalar), do'kon, lug'at (SRS), klublar, imtihon natijalarini qayd etish, sertifikatlar,
to'lovlar, hisobotlar, AI yordamchi va **tugmali Telegram bot**.

* **100% bepul ishlaydi** — karta, pulli xizmat, Node/React kerak emas.
* **Faqat 2 ta kutubxona**: `Flask` va `requests`. Ma'lumotlar bazasi — SQLite (Python bilan birga keladi).
* **Versiyaga bog'liq emas** — Python 3.8 va undan yangi (sinalgan: Python 3.13, Flask 3.1, Werkzeug 3.1, requests 2.34), Windows / macOS / Linux.
* **5 til**: o'zbek (lotin), o'zbek (kirill), rus, ingliz, koreys. Yorug' / qorong'i / tizim rejimi.
* Pullik xizmatlarga o'tish — faqat `.env` faylini o'zgartirish (kodni tegmasdan).

---

## 1. Tez boshlash (terminal)

```bash
git clone <repo-manzili> tillar-markazi && cd tillar-markazi
python -m venv .venv                     # (ixtiyoriy, lekin tavsiya etiladi)
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                     # Windows: copy .env.example .env
python manage.py init-db                 # bazani yaratadi
python manage.py create-admin            # birinchi administratorni yaratadi (parol so'raydi)
python manage.py run                     # http://127.0.0.1:5000
```

Sinab ko'rish uchun tayyor demo ma'lumotlar (o'quvchilar, darslar, savollar):

```bash
python manage.py seed-demo
```

Demo loginlar (parol hammasida `Demo12345`): `demo_admin`, `demo_ali` (o'qituvchi), `demo_olga` (o'qituvchi),
`demo_s1` … `demo_s6` (o'quvchilar), `demo_parent`. ⚠️ Demo hisoblarni haqiqiy serverda ishlatmang.

Foydali buyruqlar:

| Buyruq | Vazifasi |
|---|---|
| `python manage.py check` | sozlamalar va integratsiyalar holatini ko'rsatadi |
| `python manage.py backup` | bazaning zaxira nusxasi (`backups/`) |
| `python manage.py tick` | rejalashtirilgan ishlarni bir marta bajaradi (eslatma, jarima, battle muddatlari) |
| `python manage.py bot` | Telegram botni lokal ishga tushiradi (polling) |
| `python manage.py set-webhook` | hosting uchun webhook o'rnatadi (`--delete` — o'chiradi) |
| `python -m pytest` | testlar (`pip install -r requirements-dev.txt`) |
| `python tools/smoke.py` | barcha sahifalarni barcha rollar bilan ochib tekshiradi |

---

## 2. Telegram bot (bepul)

1. Telegramda **@BotFather** → `/newbot` → token oling.
2. `.env` ga yozing: `TELEGRAM_BOT_TOKEN=...` va `TELEGRAM_BOT_USERNAME=...`
3. **Lokal**: `python manage.py bot` (webhook shart emas).
4. **Hosting** (https): `BASE_URL=https://domeningiz`, `TELEGRAM_WEBHOOK_SECRET=<tasodifiy>` qo'ying va `python manage.py set-webhook` ni ishga tushiring.
5. Foydalanuvchi saytda **Profil → Telegram → Kod olish** ni bosadi va botga `/link KOD` yuboradi.

Bot: bugungi dars, jadval, uy vazifalari, ballar, reyting, battle takliflari (qabul/rad), do'kon, «darsga kela olmayman»,
farzandlar (ota-ona), so'rovlarni ko'rib chiqish (o'qituvchi), statistika (administrator), 5 tilda.

---

## 3. Bepul hostingga joylash

Eng oson yo'l — **PythonAnywhere** (bepul tarif) yoki **Render** (bepul web service). Ikkalasi ham karta so'ramaydi.

* **PythonAnywhere**: kodni `git clone` qiling → virtualenv + `pip install -r requirements.txt` → Web tabida *Manual config* →
  WSGI faylga `from wsgi import app as application` yozing → `.env` yarating (`APP_ENV=production`, `SECRET_KEY=...`).
* **Render / boshqa PaaS**: Start command: `gunicorn wsgi:app` (`Procfile` tayyor). Bepul tariflarda disk vaqtinchalik
  bo'lishi mumkin — doimiy disk (yoki tashqi baza) ulang va muntazam `backup` oling.
* **Docker**: `docker build -t tillar . && docker run -p 8000:8000 -v tillar-data:/data tillar`

**Rejalashtiruvchi (fon jarayonsiz).** Bepul hostinglarda doimiy fon jarayoni yo'q, shuning uchun tizim 3 yo'l bilan ishlaydi:
1. sayt ochilganda har 60 soniyada o'zi tekshiradi (`OPPORTUNISTIC_TICK=1`);
2. bepul tashqi cron (masalan cron-job.org) har 1–5 daqiqada `https://domen/internal/tick?token=TICK_TOKEN` ni chaqiradi;
3. `python manage.py tick` (server cron'ida).

---

## 4. Bepuldan pulliga o'tish (kodsiz, `.env` orqali)

| Xizmat | Bepul (standart) | Pullikka o'tish |
|---|---|---|
| AI yordamchi | `none` yoki `gemini` / `groq` (bepul limit) | `AI_PROVIDER=openai_compat` + `AI_BASE_URL` + `AI_API_KEY` + `AI_MODEL`, yoki `anthropic` |
| Email | `console` (logga yoziladi) | `EMAIL_PROVIDER=smtp` + `SMTP_*` (Gmail ilova paroli yoki istalgan pullik SMTP) |
| To'lov | `manual` (administrator qo'lda belgilaydi) | `PAYMENT_PROVIDER=click` / `payme` — shartnomadan keyin `smartedu/providers/payments.py` ichida sinf yoziladi (interfeys tayyor) |
| Telegram | bepul Bot API | o'zgarish shart emas |
| Baza | SQLite fayli (WAL rejimi) | **PostgreSQL hozircha qo'llab-quvvatlanmaydi** (SQL SQLite xususiyatlaridan foydalanadi: `INSERT OR IGNORE`, `PRAGMA`, `strftime`). Rejadagi ish. |
| Hosting | bepul PaaS | `gunicorn wsgi:app` bilan istalgan VPS |

---

## 5. Loyiha tuzilmasi

```
manage.py            CLI (init-db, create-admin, run, bot, tick, backup, check ...)
wsgi.py              gunicorn / PythonAnywhere kirish nuqtasi
smartedu/
  __init__.py        Flask app factory, xavfsizlik sarlavhalari, xatolik sahifalari
  config.py          .env dan sozlamalar          db.py + schema.sql   SQLite qatlami
  security.py        parol, sessiya, CSRF, rollar  i18n.py + translations/*.json   5 til
  services/          biznes mantiq (points, attendance, homework, battle, rating, shop, finance ...)
  providers/         tashqi xizmatlar (telegram, ai, notify/email, payments) — almashtiriladigan
  blueprints/        sahifalar: main, auth, account, student, teacher, parent, admin, battle
  bot/               Telegram tugmali bot
  templates/ static/ HTML/CSS/JS (framework yo'q, inline skript yo'q — qat'iy CSP)
tools/               smoke.py, i18n_scan.py, build_translations.py
tests/               pytest testlari
docs/ARCHITECTURE.md arxitektura diagrammasi (Mermaid)
```

Tarjimalar `smartedu/translations/src/*.tsv` da yoziladi (`kalit|uz|en|ru|ko`), keyin
`python tools/build_translations.py` JSON fayllarni (va kirill variantini avtomatik) yaratadi.

---

## 5a. Windows 10/11 bo'yicha qisqa yo'riqnoma

1. Python'ni https://www.python.org/downloads/ dan o'rnating (o'rnatishda **Add Python to PATH** belgisini qo'ying).
2. Loyiha papkasida PowerShell oching:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   python manage.py run
   ```
3. Brauzerda `http://127.0.0.1:5000` oching. Testlar: `pip install -r requirements-dev.txt` so'ng `python -m pytest -v`.

## 5b. Ishlab chiqish va production

* **Development**: `APP_ENV=development`. Demo hisoblar faqat o'zingizning kompyuteringizda uchun — **haqiqiy serverda ishlatmang**.
* **Production**: `APP_ENV=production`, uzun tasodifiy `SECRET_KEY`, `SESSION_COOKIE_SECURE=1` (https), `BASE_URL`, `TICK_TOKEN`. DEBUG production'da avtomatik o'chiriladi.
* **Birinchi administrator**: demo ma'lumotsiz bazada administrator yarating (`python manage.py --help` dagi tegishli buyruq) va parolni darhol almashtiring.
* **Orqa proksi** (nginx va h.k.) ortida `TRUSTED_PROXIES` ni sozlang, aks holda IP bo'yicha cheklov noto'g'ri IP'ni ko'radi.
* **Zaxira va tiklash**: `python manage.py backup` / `python manage.py restore <fayl>`. Migratsiya oldidan avtomatik nusxa `backups/` ga olinadi.
* **To'lov webhook**: `PAYMENT_PROVIDER=webhook` va `PAYMENT_SECRET`; `POST /payments/webhook`, sarlavha `X-Signature: sha256=<HMAC-SHA256 hex>`. Takroriy `external_id` e'tiborga olinmaydi.
* Sozlamalar: `LOGIN_IP_LIMIT`, `SESSION_MAX_DAYS`, `SESSION_IDLE_DAYS`, `TRUSTED_PROXIES` (qarang `.env.example`).

## 6. Xavfsizlik va ishonchlilik

parollar `werkzeug` bilan xeshlanadi · server tomonlama sessiyalar (tokenning xeshi saqlanadi) · barcha POST'larda CSRF ·
ko'p xato urinishdan keyin vaqtincha bloklash · rol bo'yicha ruxsat · qat'iy CSP (`script-src 'self'`) · fayl yuklashda turini cheklash ·
audit jurnali · Battle vaqti faqat server tomonida hisoblanadi (texnik sabab bilan kelmaslik reytingga ta'sir qilmaydi — «bekor» bo'ladi) ·
`?`-parametrli SQL (injection yo'q) · Jinja avtomatik escape.

## 7. Ma'lum cheklovlar

* SQLite — bitta serverli o'rta hajmli markaz uchun yetarli; juda katta yuklamada (yuzlab bir vaqtdagi foydalanuvchi) alohida baza serveri kerak bo'ladi — bu hozircha loyihada yo'q (qarang: «Amalga oshirilmaganlar»).
* Battle WebSocket'siz, qisqa so'rovlar (polling) bilan ishlaydi — bepul hostinglarda ishonchli.
* Click/Payme — **amalga oshirilmagan**; haqiqiy ulanish uchun provayder shartnomasi va kalitlar kerak. Hozir: `manual` va imzolangan `webhook`.
* Onlayn test/imtihon topshirish moduli (savollar to'plami, taymer, urinishlar) **yo'q**; imtihon natijalari o'qituvchi tomonidan qo'lda kiritiladi.
* Sertifikatda QR-kod yo'q; PDF brauzerning «Chop etish → PDF» orqali olinadi. Tekshirish sahifasi mavjud.
* To'liq ro'yxat: `docs/NOT_IMPLEMENTED.md`.
* Nutq (TTS) brauzerning o'z imkoniyatidan foydalanadi; ba'zi brauzerlarda tegishli til ovozi bo'lmasligi mumkin.

Litsenziya: MIT.
