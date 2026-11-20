# Xatolar hisoboti

| # | Muammo | Tuzatish | Test |
|---|--------|----------|------|
| 1 | `sum_between` bekor qilinganlarni ham qo'shardi | `revoked=0` | test_services |
| 2 | `revoke` takroran/parallel Coin ayirardi | atomik shartli UPDATE | test_services |
| 3 | PostgreSQL va'dasi yolg'on | olib tashlandi | test_database |
| 4 | Migratsiya xatosida baza buzilishi | tranzaksiya + zaxira | test_database |
| 5 | Restore yo'q | `manage.py restore` | test_database |
| 6 | Login cheklovi faqat akkauntga | IP bo'yicha ham | test_security |
| 7 | Sessiya muddatsiz | 30/14 kun | test_security |
| 8 | Redirect `/\evil.com` | `is_safe_target` | test_security |
| 9 | Yuklashda faqat kengaytma | magic-bytes + sandbox CSP | test_security |
| 10 | «Chop etish» CSP tufayli ishlamasdi | inline handler olib tashlandi | test_web |
| 11 | Oylik hisobotda ikki marta sanash | to'g'rilandi | test_finance |
| 12 | Qisman/ortiqcha to'lov, webhook dublikati | yangi moliya xizmati | test_finance |
| 13 | Battle `set_ready` takroriy so'rov | holat tx ichida qayta o'qiladi | test_battle |
| 14 | `.env` izohlari qiymatga qo'shilardi | parser | test_web |

Natija: `python -m pytest` — **122 passed** (~96 s, Python 3.13). `tools/smoke.py` — 113 sahifa, 0 xato. `tools/i18n_scan.py --missing` — 0.
