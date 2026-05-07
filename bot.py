import asyncio
import logging
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from monitor import PolymarketMonitor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_ADDRESS = 1
HOURLY_INTERVAL = 3600  # 1 година

monitor = PolymarketMonitor()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Polymarket Trade Monitor*\n\n"
        "Цей бот відстежує угоди трейдерів на Polymarket "
        "і надсилає сповіщення у реальному часі.\n\n"
        "*Команди:*\n"
        "▸ /add — додати адресу трейдера\n"
        "▸ /list — список трейдерів\n"
        "▸ /remove — видалити трейдера\n"
        "▸ /positions — переглянути позиції зараз\n"
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
        "3. Кожну годину — автоматичний звіт по позиціях з PnL\n\n"
        "*Формат адреси:*\n"
        "`0x1234...abcd` (42 символи, починається з 0x)\n\n"
        "*Де знайти адресу:*\n"
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
            f"Сповіщення: нові угоди + щогодинний звіт по позиціях.",
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
        await update.message.reply_text("📋 Список порожній.\n\nДодайте трейдера через /add")
        return

    lines = ["📋 *Трейдери під моніторингом:*\n"]
    for i, trader in enumerate(traders, 1):
        addr = trader["address"]
        lines.append(f"{i}. `{addr[:10]}...{addr[-6:]}`")

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
        keyboard.append([InlineKeyboardButton(f"🗑 {short}", callback_data=f"remove:{addr}")])

    await update.message.reply_text(
        "Оберіть трейдера для видалення:",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
        await query.edit_message_text(f"✅ Трейдера `{short}` видалено.", parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)
    total = monitor.get_total_traders()

    await update.message.reply_text(
        f"📊 *Статус моніторингу*\n\n"
        f"🟢 Бот активний\n"
        f"👤 Ваших трейдерів: {len(traders)}\n"
        f"🌐 Всього трейдерів: {total}\n"
        f"⏱ Інтервал перевірки: 30 сек\n"
        f"📈 Звіт по позиціях: щогодини",
        parse_mode="Markdown"
    )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger for positions report."""
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text("📋 Список порожній. Додайте трейдера через /add")
        return

    await update.message.reply_text("⏳ Завантажую позиції...")

    for trader in traders:
        address = trader["address"]
        report = await monitor.get_positions_report(address)
        await send_positions_report(update.get_bot(), chat_id, address, report)


async def send_positions_report(bot, chat_id: str, address: str, positions: list):
    """Send hourly positions report for a trader."""
    short_addr = f"`{address[:10]}...{address[-6:]}`"
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    if not positions:
        text = (
            f"📊 *Звіт по позиціях*\n"
            f"👤 {short_addr}\n"
            f"🕐 {now}\n\n"
            f"— Відкритих позицій немає"
        )
        try:
            await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send positions report: {e}")
        return

    total_value = sum(p.get("current_value", 0) for p in positions)
    total_pnl = sum(p.get("pnl", 0) for p in positions)
    total_invested = sum(p.get("invested", 0) for p in positions)
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    pnl_sign = "+" if total_pnl >= 0 else ""

    lines = [
        f"📊 *Щогодинний звіт по позиціях*",
        f"👤 {short_addr}",
        f"🕐 {now}",
        f"",
        f"💼 Всього позицій: {len(positions)}",
        f"💰 Загальна вартість: *${total_value:.2f}*",
        f"{pnl_emoji} Загальний PnL: *{pnl_sign}${total_pnl:.2f}* ({pnl_sign}{total_pnl_pct:.1f}%)",
        f"",
        f"─────────────────",
    ]

    for i, pos in enumerate(positions[:10], 1):  # max 10 позицій
        title = pos.get("market_title", "—")
        title = title[:40] + "..." if len(title) > 40 else title
        outcome = pos.get("outcome", "")
        current_val = pos.get("current_value", 0)
        pnl = pos.get("pnl", 0)
        pnl_pct = pos.get("pnl_pct", 0)
        cur_price = pos.get("current_price", 0)
        avg_price = pos.get("avg_price", 0)
        p_emoji = "🟢" if pnl >= 0 else "🔴"
        p_sign = "+" if pnl >= 0 else ""
        market_url = pos.get("market_url", "")

        lines.append(f"\n*{i}. {title}*")
        lines.append(f"   📌 {outcome} | Ціна: {avg_price:.3f} → {cur_price:.3f}")
        lines.append(f"   💵 ${current_val:.2f} | {p_emoji} PnL: {p_sign}${pnl:.2f} ({p_sign}{pnl_pct:.1f}%)")
        if market_url:
            lines.append(f"   [🔗 Відкрити]({market_url})")

    if len(positions) > 10:
        lines.append(f"\n_... та ще {len(positions) - 10} позицій_")

    text = "\n".join(lines)

    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to send positions report to {chat_id}: {e}")


async def send_trade_notification(bot, chat_id: str, trade: dict):
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

    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    except Exception as e:
        logger.error(f"Failed to send notification to {chat_id}: {e}")


async def run_monitor_loop(app):
    """Background loop: checks trades every 30s, sends positions report every hour."""
    logger.info("Monitor loop started")
    last_report_time = 0

    while True:
        try:
            # --- Перевірка нових угод ---
            new_trades = await monitor.check_new_trades()
            for chat_id, trade in new_trades:
                await send_trade_notification(app.bot, chat_id, trade)

            # --- Щогодинний звіт по позиціях ---
            now = asyncio.get_event_loop().time()
            if now - last_report_time >= HOURLY_INTERVAL:
                last_report_time = now
                logger.info("Sending hourly positions report...")
                for chat_id, trader_list in monitor.traders.items():
                    for trader in trader_list:
                        address = trader["address"]
                        positions = await monitor.get_positions_report(address)
                        await send_positions_report(app.bot, chat_id, address, positions)

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
        states={WAITING_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_callback))

    async def post_init(application):
        asyncio.create_task(run_monitor_loop(application))

    app.post_init = post_init

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
