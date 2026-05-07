import asyncio
import logging
import os
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from monitor import PolymarketMonitor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_ADDRESS = 1

monitor = PolymarketMonitor()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Polymarket Trade Monitor*\n\n"
        "Цей бот відстежує відкриті угоди трейдерів на Polymarket "
        "і надсилає сповіщення у реальному часі.\n\n"
        "*Команди:*\n"
        "▸ /add — додати адресу трейдера\n"
        "▸ /list — список трейдерів, що відстежуються\n"
        "▸ /remove — видалити трейдера\n"
        "▸ /status — статус моніторингу\n"
        "▸ /help — допомога\n\n"
        "Напишіть /add щоб розпочати."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Довідка*\n\n"
        "*Як користуватися:*\n"
        "1. Введіть /add і вставте адресу гаманця трейдера\n"
        "2. Бот почне моніторити нові угоди\n"
        "3. При кожній новій угоді ви отримаєте сповіщення\n\n"
        "*Формат адреси:*\n"
        "`0x1234...abcd` (42 символи, починається з 0x)\n\n"
        "*Де знайти адресу трейдера:*\n"
        "Polymarket → профіль трейдера → скопіюйте адресу з URL\n"
        "`polymarket.com/profile/0x...`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Введіть адресу гаманця трейдера на Polymarket:\n\n"
        "Приклад: `0xd5B86E84Be3bC0BD2D5A3D5f9b3b5a8b3c9e0f1a`",
        parse_mode="Markdown"
    )
    return WAITING_ADDRESS


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip().lower()

    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text(
            "❌ Невірний формат адреси.\n\n"
            "Адреса повинна починатися з `0x` і мати 42 символи.\n"
            "Спробуйте ще раз або /cancel для скасування.",
            parse_mode="Markdown"
        )
        return WAITING_ADDRESS

    chat_id = str(update.effective_chat.id)
    result = monitor.add_trader(chat_id, address)

    if result == "exists":
        await update.message.reply_text(
            f"ℹ️ Трейдер `{address[:10]}...{address[-6:]}` вже відстежується.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"✅ Трейдера додано!\n\n"
            f"Адреса: `{address[:10]}...{address[-6:]}`\n"
            f"Моніторинг активний 🟢\n\n"
            f"Ви отримаєте сповіщення при кожній новій угоді.",
            parse_mode="Markdown"
        )
        logger.info(f"Added trader {address} for chat {chat_id}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text(
            "📋 Список порожній.\n\nДодайте трейдера через /add"
        )
        return

    lines = ["📋 *Трейдери під моніторингом:*\n"]
    for i, trader in enumerate(traders, 1):
        addr = trader["address"]
        short = f"`{addr[:10]}...{addr[-6:]}`"
        lines.append(f"{i}. {short}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text("📋 Список порожній. Нічого видаляти.")
        return

    keyboard = []
    for trader in traders:
        addr = trader["address"]
        short = f"{addr[:10]}...{addr[-6:]}"
        keyboard.append([InlineKeyboardButton(
            f"🗑 {short}",
            callback_data=f"remove:{addr}"
        )])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Оберіть трейдера для видалення:",
        reply_markup=reply_markup
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    data = query.data

    if data.startswith("remove:"):
        address = data.split(":", 1)[1]
        monitor.remove_trader(chat_id, address)
        short = f"{address[:10]}...{address[-6:]}"
        await query.edit_message_text(
            f"✅ Трейдера `{short}` видалено.",
            parse_mode="Markdown"
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)
    total = monitor.get_total_traders()

    await update.message.reply_text(
        f"📊 *Статус моніторингу*\n\n"
        f"🟢 Бот активний\n"
        f"👤 Ваших трейдерів: {len(traders)}\n"
        f"🌐 Всього трейдерів: {total}\n"
        f"⏱ Інтервал перевірки: 30 сек",
        parse_mode="Markdown"
    )


async def send_trade_notification(app, chat_id: str, trade: dict):
    """Send notification about a new trade or closed position."""
    market_url = trade.get("market_url", "")
    market_title = trade.get("market_title", "Невідомий ринок")
    outcome = trade.get("outcome", "")
    usd_value = trade.get("usd_value", 0)
    trader = trade.get("trader_address", "")
    timestamp = trade.get("timestamp", "")
    event_type = trade.get("event_type", "open")

    if event_type == "close":
        avg_price = trade.get("avg_price", 0)
        size = trade.get("size", 0)
        text = (
            f"🔕 *Позицію закрито!*\n\n"
            f"👤 Трейдер: `{trader[:10]}...{trader[-6:]}`\n\n"
            f"📌 *{market_title}*\n\n"
            f"❌ Закрито — {outcome}\n"
            f"💰 Сума: *${usd_value:.2f}*\n"
            f"📊 Ціна входу: {avg_price:.3f} | Розмір: {size:.2f}\n"
            f"🕐 {timestamp}"
        )
    else:
        side = trade.get("side", "")
        size = trade.get("size", 0)
        price = trade.get("price", 0)
        side_emoji = "🟢" if side.upper() == "BUY" else "🔴"
        side_text = "КУПІВЛЯ" if side.upper() == "BUY" else "ПРОДАЖ"
        text = (
            f"🔔 *Нова угода!*\n\n"
            f"👤 Трейдер: `{trader[:10]}...{trader[-6:]}`\n\n"
            f"📌 *{market_title}*\n\n"
            f"{side_emoji} *{side_text}* — {outcome}\n"
            f"💰 Сума: *${usd_value:.2f}*\n"
            f"📊 Ціна: {price:.3f} | Розмір: {size:.2f}\n"
            f"🕐 {timestamp}"
        )

    keyboard = []
    if market_url:
        keyboard.append([InlineKeyboardButton("🔗 Відкрити на Polymarket", url=market_url)])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    try:
        await app.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to send notification to {chat_id}: {e}")


async def run_monitor_loop(app):
    """Background loop that checks for new trades."""
    logger.info("Monitor loop started")
    while True:
        try:
            new_trades = await monitor.check_new_trades()
            for chat_id, trade in new_trades:
                await send_trade_notification(app, chat_id, trade)
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        await asyncio.sleep(30)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            WAITING_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(conv_handler)

    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(button_callback))

    async def post_init(application):
        asyncio.create_task(run_monitor_loop(application))

    app.post_init = post_init

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
