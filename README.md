# 🤖 Polymarket Trade Monitor Bot

Telegram бот для моніторингу угод трейдерів на Polymarket.  
Надсилає сповіщення при кожній новій угоді з посиланням на подію.

---

## 📦 Структура проєкту

```
polymarket-bot/
├── bot.py           # Основна логіка бота
├── monitor.py       # Моніторинг через Polymarket API
├── requirements.txt
├── Dockerfile
├── railway.toml     # Конфіг для деплою на Railway
└── .env.example
```

---

## 🚀 Деплой на Railway (рекомендовано — безкоштовно, сервери поза Україною)

### 1. Створити Telegram бота

1. Відкрийте [@BotFather](https://t.me/BotFather) в Telegram
2. Надішліть `/newbot`
3. Оберіть назву і username для бота
4. Скопіюйте **API токен** — він виглядає так: `7123456789:AAF...`

### 2. Залити код на GitHub

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/polymarket-bot.git
git push -u origin main
```

### 3. Деплой на Railway

1. Зайдіть на [railway.app](https://railway.app) → **Sign in with GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. Оберіть ваш репозиторій
4. Перейдіть в **Variables** і додайте:
   ```
   TELEGRAM_BOT_TOKEN = ваш_токен_від_botfather
   ```
5. Railway автоматично збере Docker-образ і запустить бота

✅ Сервери Railway знаходяться в США/Європі — гео-блок Polymarket не діє.

---

## 💻 Локальний запуск (для тестування)

> Потрібен Python 3.10+

```bash
# 1. Клонуйте репо
git clone https://github.com/YOUR_USERNAME/polymarket-bot.git
cd polymarket-bot

# 2. Встановіть залежності
pip install -r requirements.txt

# 3. Створіть .env
cp .env.example .env
# Відредагуйте .env і вставте токен

# 4. Запустіть
python bot.py
```

---

## 📱 Команди бота

| Команда | Опис |
|---------|------|
| `/start` | Запуск бота |
| `/add` | Додати адресу трейдера |
| `/list` | Список трейдерів |
| `/remove` | Видалити трейдера |
| `/status` | Статус моніторингу |
| `/help` | Довідка |

---

## 🔔 Приклад сповіщення

```
🔔 Нова угода!

👤 Трейдер: 0xd5B86E84...f1a

📌 Will Trump win the 2024 election?

🟢 КУПІВЛЯ — Yes
💰 Сума: $250.00
📊 Ціна: 0.625 | Розмір: 400.00
🕐 15.11.2024 14:32 UTC

[🔗 Відкрити на Polymarket]
```

---

## ⚙️ Як працює

1. Бот зберігає список трейдерів у `traders.json`
2. Кожні **30 секунд** перевіряє нові угоди через [Polymarket CLOB API](https://docs.polymarket.com)
3. При знаходженні нових угод — надсилає сповіщення з деталями
4. Перша перевірка лише записує поточний стан (без сповіщень) — щоб не спамити старими угодами

---

## 🔧 Альтернативний деплой — Render.com

1. [render.com](https://render.com) → **New** → **Web Service**
2. Підключіть GitHub репо
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py`
5. Environment: Python 3
6. Додайте `TELEGRAM_BOT_TOKEN` в Environment Variables
7. Оберіть **Free** план

---

## 📝 Примітки

- Polymarket CLOB API публічний і не потребує ключів
- Дані зберігаються локально у `traders.json` (на Railway — в контейнері)
- Для persistent storage на Railway можна підключити Railway Volume
