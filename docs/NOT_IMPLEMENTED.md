# Amalga oshirilmagan yoki tashqi xizmatga bog'liq qismlar

1. **Onlayn test/imtihon moduli** (savol to'plami, guruhga biriktirish, taymer, urinishlar, avto-baholash) — YO'Q. Faqat natijalarni qo'lda kiritish bor.
2. **PostgreSQL** — qo'llab-quvvatlanmaydi (faqat SQLite).
3. **Click / Payme** — haqiqiy ulanish yo'q (shartnoma va kalitlar kerak). Bor: qo'lda to'lov va HMAC webhook.
4. **Sertifikatda QR-kod** — yo'q; PDF brauzer orqali; tekshirish URL bor.
5. **WebSocket** — yo'q; Battle polling bilan.
6. **AI** — `AI_API_KEY` kerak; kalitsiz «o'chiq» holat.
7. **Telegram bot** — token kerak; haqiqiy Telegram bilan sinalmagan, faqat birlik testlar.
8. **Email** — `console` yoki SMTP; haqiqiy SMTP bilan sinalmagan.
9. **Chuqur audit qilinmagan**: lug'at SRS, bildirishnoma takrorlanishi/sozlamalari, uy vazifasi jarimasi nizolari, mobil/qorong'i rejim vizual tekshiruvi.
