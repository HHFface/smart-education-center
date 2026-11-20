# CHANGELOG

## Audit versiyasi (SMART_EDUCATION_CENTER_FIXED)
### Tuzatildi
- Ball tizimi: `sum_between` bekor qilinganlarni hisoblamaydi; `revoke` atomik va takrorlanmas; Coin manfiy bo'lmaydi.
- Baza: SAVEPOINT, WAL, versiyalangan migratsiyalar (v1→v3), migratsiya oldi avtomatik zaxira, `backup` va `restore`.
- PostgreSQL haqidagi va'da olib tashlandi (aniq xato xabari).
- Xavfsizlik: IP va akkaunt bo'yicha cheklov, `TRUSTED_PROXIES`, sessiya muddatlari, bir martalik tokenlar atomik, forgot-password bir xil javob, xavfsiz redirect, fayl magic-bytes tekshiruvi, yuklangan fayllarga sandbox CSP.
- Davomat: sababli ariza hujjati, rad etishda izoh majburiy, tasdiqlangan qaytarilmaydi, ota-onaga xabar, admin sahifasi.
- Moliya: qisman to'lov, ortiqcha to'lovni rad etish, tranzaksiyalar jurnali, bekor qilish, yillik hisobot, imzolangan webhook va dublikat himoyasi; oylik hisobotda ikki marta sanash tuzatildi.
- Sertifikat: bekor qilish va tekshiruv sahifasida holat.
- Battle: `set_ready` poygasi tuzatildi.
- `.env` izohlari, hisobotdagi «Chop etish» (CSP), admin to'lovlar «qarzdorlar» tabi.
### Qo'shildi
- Mashq rejimi (reyting/XP/Coin'ga ta'sir qilmaydi); tarjima tekshiruvi kengaytirildi (974 kalit × 5 til); 122 test.
