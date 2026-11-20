# Arxitektura

```mermaid
flowchart LR
  subgraph Client
    B[Brauzer: HTML + CSS + oddiy JS]
    T[Telegram ilovasi]
  end
  subgraph App["Flask ilovasi (smartedu)"]
    BP[blueprints: auth / student / teacher / parent / admin / battle]
    BOT[bot/handlers]
    SV[services: points, attendance, homework, battle, rating, shop, finance ...]
    PR[providers: telegram, ai, email, payments]
    DB[(db.py -> SQLite)]
    SCH[scheduler tick]
  end
  B --> BP --> SV --> DB
  T -->|webhook / polling| BOT --> SV
  SV --> PR
  PR -->|bepul: Gemini/Groq, SMTP, Telegram Bot API| EXT[Tashqi xizmatlar]
  CRON[Bepul cron / manage.py tick] --> SCH --> SV
```

* Hamma SQL `db.py` orqali o'tadi (bitta joyda tranzaksiya, SAVEPOINT, migratsiya, zaxira). Baza — faqat SQLite; PostgreSQL qo'llab-quvvatlanmaydi.
* `providers/` — bepul variant standart; `.env` orqali pulliga almashadi.
* Battle: server vaqt chizig'i (started_at + i·(sek+tanaffus)), polling API, Elo (K 40 / 24), ligalar.
