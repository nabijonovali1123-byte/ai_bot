# Telegram Business AI yordamchi

Bu loyiha Telegram Business hisobiga ulangan tanlangan suhbatlar uchun qisqa javob loyihalarini tayyorlaydi. Javoblar egasining profili va ayni suhbatdagi oxirgi 30 tagacha xabarga asoslanadi. Boshqa chatlar kontekst sifatida ishlatilmaydi.

## Muhim

- Dastlab `AUTO_SEND=false`: javoblar yuborilmaydi, faqat `data/previews.jsonl` va loglarga yoziladi.
- Telegram Business → Chatbots ichida botga qaysi chatlarni ko‘rish va ularga javob berish huquqini berishni o‘zingiz tanlaysiz.
- AI uchun OpenAI API kaliti ishlatilmaydi. Egasi brauzerda Sign in with ChatGPT orqali OAuth ruxsatini beradi; bu ChatGPT obuna limitidan foydalanadi.
- Token, OAuth ma’lumotlari va shaxsiy profilni hech kimga chat orqali yubormang. Profil bot ruxsat olgan chatlarga berilishi mumkin bo‘lgan ma’lumotlarnigina o‘z ichiga olsin.
- Ishga tushirish uchun BotFather bergan Telegram bot tokeni va Telegram foydalanuvchi raqamingiz (`OWNER_ID`) kerak. Ularni faqat o‘z kompyuteringizdagi `.env` fayliga yozing.

## 1. Kerakli dasturlar

Kompyuterga Python 3.9 yoki yangiroq versiyasini o‘rnating. Loyiha papkasida terminal ochib:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
chmod 600 .env
```

Windows’da `.venv\\Scripts\\python.exe` va `.venv\\Scripts\\pip.exe` dan foydalaning; `.env` faylini matn muharririda yarating.

## 2. Telegram sozlamalari

1. Telegram’da `@BotFather` orqali bot yarating va token oling. Tokenni xavfsiz saqlang.
2. O‘z raqamli Telegram ID’ingizni aniqlang; bu telefon raqamingiz emas.
3. `.env` faylida `TELEGRAM_BOT_TOKEN=` va `OWNER_ID=` qatorlarini to‘ldiring. Token yoki ID’ni chatga yubormang.
4. `AUTO_SEND=false` holida qoldiring.

## 3. ChatGPT hisobini ulash

Ushbu kompyuterda quyidagini bajaring:

```bash
.venv/bin/python bot.py --connect-codex
```

Brauzer ochilganda o‘z ChatGPT hisobingiz bilan ruxsat bering. Maxfiy ma’lumotlar `data/codex_credentials.json` ichida himoyalangan holda saqlanadi. Bu faylni chatga yubormang.

## 4. Profilni to‘ldirish va tekshirish

`profile.example.md` dan nusxa olib `profile.md` nomi bilan saqlang. Faqat suhbatdoshlar bilan bo‘lishishga rozi bo‘lgan xavfsiz ma’lumotlarni kiriting. Parol, tasdiqlash kodi, karta yoki hisob raqami, hujjat ma’lumoti, telefon va aniq uy manzilini yozmang.

```bash
.venv/bin/python bot.py --check-config
.venv/bin/python bot.py --check-codex
```

Bitta javobni tekshirish `--try` orqali ChatGPT obuna limitidan foydalanadi:

```bash
.venv/bin/python bot.py --try "Salom, xizmatingiz haqida ma'lumot bering"
```

Botni ishga tushiring:

```bash
.venv/bin/python bot.py
```

Botga shaxsiy chatda `/start` yuboring. Keyin Telegram Settings → Telegram Business → Chatbots bo‘limida uni ulang va kerakli suhbatlarga ruxsat bering. Botni to‘xtatish uchun terminalda `Ctrl+C` bosing.

## 5. Javoblarni yuborish

`data/previews.jsonl` fayli va loglarni avval ko‘rib chiqing. Bot `/pause`, `/resume`, `/status` buyruqlarini egasining bot bilan shaxsiy chatida qabul qiladi. Javoblar ma’qul bo‘lib, ularni yuborishni xohlasangizgina `.env` ichida `AUTO_SEND=true` qiling va botni qayta ishga tushiring. Auto-send yoqilganda ham bot ulanish va javob berish ruxsatini yuborishdan oldin tekshiradi.

## Ixtiyoriy: Railway’da ishga tushirish

1. Kodni maxfiy fayllarsiz GitHub repozitoriyga joylang. `.gitignore` va `.dockerignore` `.env`, `data/`, `profile.md` va testlarni chiqarib tashlaydi.
2. Railway’da Dockerfile orqali xizmat yarating va `/app/data` manziliga doimiy Volume ulang.
3. Variables bo‘limida `TELEGRAM_BOT_TOKEN`, `OWNER_ID`, `AUTO_SEND=false` ni kiriting. OAuth jarayonini avval mahalliy kompyuterda yakunlang. `CODEX_CREDENTIALS_JSON` va `PROFILE_CONTENT` ni faqat birinchi import uchun maxfiy Variables sifatida kiriting.
4. Bot ularni `/app/data` ichiga ko‘chirganini tekshirib, bootstrap Variables’ni o‘chiring. Volume doimiy saqlanishini ta’minlang.
5. Bir bot tokeni uchun faqat bitta bot jarayonini ishlating.

Railway’ni sozlash uchun alohida hisob kerak bo‘lishi mumkin va hosting pulli bo‘lishi mumkin.

## Foydali buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `python bot.py` | Botni ishga tushiradi |
| `python bot.py --check-config` | Sozlamalarni maxfiy qiymatlarni ko‘rsatmasdan tekshiradi |
| `python bot.py --check-codex` | ChatGPT ulanishi va modellarning mavjudligini tekshiradi |
| `python bot.py --connect-codex` | Brauzer orqali OAuth ulanishini boshlaydi |
| `python bot.py --try "matn"` | Bitta sinov javobini yaratadi |

## Oflayn testlar

```bash
.venv/bin/pytest
```

Testlar Telegram yoki OpenAI’ga haqiqiy so‘rov yubormaydi.
