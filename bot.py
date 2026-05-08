import asyncio
import logging
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

# Стани розмови
WAITING_ADDRESS = 1
WAITING_NICKNAME = 2
WAITING_NICKNAME_FOR = 3

HOURLY_INTERVAL = 3600

monitor = PolymarketMonitor()

# Закріплені повідомлення {chat_id: message_id}
pinned_messages = {}


def main_keyboard():
    """Головна клавіатура з кнопками внизу."""
    keyboard = [
        [KeyboardButton("➕ Додати трейдера"), KeyboardButton("📋 Мої трейдери")],
        [KeyboardButton("📊 Позиції зараз"), KeyboardButton("🏷 Нікнейм")],
        [KeyboardButton("📈 Статус"), KeyboardButton("❓ Допомога")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Polymarket Trade Monitor*\n\n"
        "Відстежую угоди трейдерів на Polymarket і надсилаю сповіщення у реальному часі.\n\n"
        "Використовуй кнопки внизу або команди:\n"
        "/add · /list · /positions · /nickname · /status"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Довідка*\n\n"
        "➕ *Додати трейдера* — почати відстежувати адресу\n"
        "📋 *Мої трейдери* — список трейдерів\n"
        "📊 *Позиції зараз* — відкриті позиції з PnL\n"
        "🏷 *Нікнейм* — назвати трейдера зручно\n"
        "📈 *Статус* — чи працює бот\n\n"
        "🔔 Сповіщення приходять автоматично:\n"
        "▸ Нова угода — одразу\n"
        "▸ Закрита позиція — одразу\n"
        "▸ Звіт по позиціях — щогодини\n\n"
        "*Де знайти адресу трейдера:*\n"
        "`polymarket.com/profile/0x...`"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Введіть адресу гаманця трейдера:\n\n"
        "Приклад: `0xd5B86E84Be3bC0BD2D5A3D5f9b3b5a8b3c9e0f1a`\n\n"
        "або /cancel для скасування",
        parse_mode="Markdown"
    )
    return WAITING_ADDRESS


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip().lower()

    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text(
            "❌ Невірний формат. Адреса починається з `0x` і має 42 символи.\n"
            "Спробуйте ще раз або /cancel",
            parse_mode="Markdown"
        )
        return WAITING_ADDRESS

    chat_id = str(update.effective_chat.id)
    result = monitor.add_trader(chat_id, address)

    if result == "exists":
        await update.message.reply_text(
            f"ℹ️ Трейдер вже відстежується.",
            reply_markup=main_keyboard()
        )
        return ConversationHandler.END

    context.user_data["new_address"] = address
    await update.message.reply_text(
        f"✅ Адресу додано!\n\n"
        f"Хочеш дати нікнейм цьому трейдеру?\n"
        f"Наприклад: *Кит №1*, *Мій акаунт*, *Друг*\n\n"
        f"Введи нікнейм або натисни /skip щоб пропустити",
        parse_mode="Markdown"
    )
    return WAITING_NICKNAME


async def receive_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    address = context.user_data.get("new_address", "")
    nickname = update.message.text.strip()

    if address:
        monitor.set_nickname(chat_id, address, nickname)

    short = f"{address[:10]}...{address[-6:]}"
    await update.message.reply_text(
        f"✅ Трейдера додано!\n\n"
        f"📍 `{short}`\n"
        f"🏷 Нікнейм: *{nickname}*\n"
        f"🟢 Моніторинг активний\n\n"
        f"Сповіщення: нові угоди + щогодинний звіт.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    logger.info(f"Added trader {address} with nickname '{nickname}' for chat {chat_id}")
    return ConversationHandler.END


async def skip_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    address = context.user_data.get("new_address", "")
    short = f"{address[:10]}...{address[-6:]}"

    await update.message.reply_text(
        f"✅ Трейдера додано!\n\n"
        f"📍 `{short}`\n"
        f"🟢 Моніторинг активний",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text(
            "📋 Список порожній.\n\nДодай трейдера через ➕",
            reply_markup=main_keyboard()
        )
        return

    lines = ["📋 *Трейдери під моніторингом:*\n"]
    for i, trader in enumerate(traders, 1):
        addr = trader["address"]
        nick = trader.get("nickname", "")
        short = f"`{addr[:10]}...{addr[-6:]}`"
        if nick:
            lines.append(f"{i}. {short} — *{nick}*")
        else:
            lines.append(f"{i}. {short}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_keyboard())


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text("📋 Список порожній.", reply_markup=main_keyboard())
        return

    keyboard = []
    for trader in traders:
        addr = trader["address"]
        nick = trader.get("nickname", "")
        label = nick if nick else f"{addr[:10]}...{addr[-6:]}"
        keyboard.append([InlineKeyboardButton(f"🗑 {label}", callback_data=f"remove:{addr}")])

    await update.message.reply_text(
        "Оберіть трейдера для видалення:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def nickname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text("📋 Список порожній.", reply_markup=main_keyboard())
        return

    keyboard = []
    for trader in traders:
        addr = trader["address"]
        nick = trader.get("nickname", "")
        label = nick if nick else f"{addr[:10]}...{addr[-6:]}"
        keyboard.append([InlineKeyboardButton(f"🏷 {label}", callback_data=f"setnick:{addr}")])

    await update.message.reply_text(
        "Оберіть трейдера для зміни нікнейму:",
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
        nick = monitor.get_nickname(chat_id, address)
        await query.edit_message_text(f"✅ Трейдера *{nick}* видалено.", parse_mode="Markdown")

    elif data.startswith("setnick:"):
        address = data.split(":", 1)[1]
        context.user_data["nick_address"] = address
        await query.edit_message_text(
            f"🏷 Введіть новий нікнейм для `{address[:10]}...{address[-6:]}`:",
            parse_mode="Markdown"
        )
        return WAITING_NICKNAME_FOR

    elif data == "show_all_positions":
        # Кнопка "показати всі позиції" — надсилаємо повний звіт
        address = context.user_data.get("full_positions_address", "")
        positions = context.user_data.get("full_positions_data", [])
        if positions and address:
            await send_positions_report(
                query.message.get_bot() if hasattr(query.message, 'get_bot') else update.get_bot(),
                chat_id, address, positions, show_all=True
            )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)
    total = monitor.get_total_traders()

    await update.message.reply_text(
        f"📈 *Статус моніторингу*\n\n"
        f"🟢 Бот активний\n"
        f"👤 Ваших трейдерів: *{len(traders)}*\n"
        f"🌐 Всього трейдерів у боті: *{total}*\n"
        f"⏱ Перевірка угод: кожні 30 сек\n"
        f"📊 Звіт позицій: щогодини",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    traders = monitor.get_traders(chat_id)

    if not traders:
        await update.message.reply_text(
            "📋 Список порожній. Додай трейдера через ➕",
            reply_markup=main_keyboard()
        )
        return

    msg = await update.message.reply_text("⏳ Завантажую позиції...")

    bot = update.get_bot()
    for trader in traders:
        address = trader["address"]
        positions = await monitor.get_positions_report(address)
        await send_positions_report(bot, chat_id, address, positions, context=context)

    try:
        await msg.delete()
    except Exception:
        pass


async def send_positions_report(bot, chat_id: str, address: str, positions: list, show_all: bool = False, context=None):
    """Надсилає звіт по позиціях. Якщо >5 — показує кнопку 'Показати всі'."""
    nick = monitor.get_nickname(chat_id, address)
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    MAX_SHOW = 5

    if not positions:
        text = (
            f"📊 *Позиції — {nick}*\n"
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
        f"📊 *Позиції — {nick}*",
        f"🕐 {now}",
        f"",
        f"💼 Позицій: *{len(positions)}*  |  💰 Вартість: *${total_value:.2f}*",
        f"{pnl_emoji} PnL: *{pnl_sign}${total_pnl:.2f}* ({pnl_sign}{total_pnl_pct:.1f}%)",
        f"",
        f"─────────────────",
    ]

    show_positions = positions if show_all else positions[:MAX_SHOW]

    for i, pos in enumerate(show_positions, 1):
        title = pos.get("market_title", "—")
        title = title[:45] + "..." if len(title) > 45 else title
        outcome = pos.get("outcome", "")
        cur_val = pos.get("current_value", 0)
        pnl = pos.get("pnl", 0)
        pnl_pct = pos.get("pnl_pct", 0)
        cur_price = pos.get("current_price", 0)
        avg_price = pos.get("avg_price", 0)
        p_emoji = "🟢" if pnl >= 0 else "🔴"
        p_sign = "+" if pnl >= 0 else ""
        market_url = pos.get("market_url", "")

        lines.append(f"\n*{i}. {title}*")
        lines.append(f"   📌 {outcome}  {avg_price:.3f} → {cur_price:.3f}")
        lines.append(f"   💵 ${cur_val:.2f}  {p_emoji} {p_sign}${pnl:.2f} ({p_sign}{pnl_pct:.1f}%)")
        if market_url:
            lines.append(f"   [🔗 Відкрити]({market_url})")

    text = "\n".join(lines)

    # Кнопка "Показати всі" якщо є ще позиції
    keyboard = []
    remaining = len(positions) - MAX_SHOW
    if not show_all and remaining > 0:
        if context:
            context.user_data["full_positions_address"] = address
            context.user_data["full_positions_data"] = positions
        keyboard.append([InlineKeyboardButton(
            f"👁 Показати ще {remaining} позицій",
            callback_data="show_all_positions"
        )])

    try:
        sent = await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
        return sent
    except Exception as e:
        logger.error(f"Failed to send positions report to {chat_id}: {e}")


async def send_trade_notification(bot, chat_id: str, trade: dict):
    market_url = trade.get("market_url", "")
    market_title = trade.get("market_title", "Невідомий ринок")
    outcome = trade.get("outcome", "")
    usd_value = trade.get("usd_value", 0)
    trader = trade.get("trader_address", "")
    timestamp = trade.get("timestamp", "")
    event_type = trade.get("event_type", "open")
    nick = monitor.get_nickname(chat_id, trader)

    if event_type == "close":
        avg_price = trade.get("avg_price", 0)
        size = trade.get("size", 0)
        text = (
            f"🔕 *Позицію закрито!*\n\n"
            f"👤 {nick}\n\n"
            f"📌 *{market_title}*\n\n"
            f"❌ Продаж — {outcome}\n"
            f"💰 Сума: *${usd_value:.2f}*\n"
            f"📊 Ціна входу: {avg_price:.3f} | Розмір: {size:.2f}\n"
            f"🕐 {timestamp}"
        )
    else:
        side = trade.get("side", "BUY")
        size = trade.get("size", 0)
        price = trade.get("price", 0)
        # BUY = купівля (відкриття), SELL = продаж (закриття через activity)
        side_emoji = "🟢" if side == "BUY" else "🔴"
        side_text = "КУПІВЛЯ" if side == "BUY" else "ПРОДАЖ"
        text = (
            f"🔔 *Нова угода!*\n\n"
            f"👤 {nick}\n\n"
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


async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник кнопок Reply Keyboard."""
    text = update.message.text

    if text == "➕ Додати трейдера":
        return await add_command(update, context)
    elif text == "📋 Мої трейдери":
        await list_command(update, context)
    elif text == "📊 Позиції зараз":
        await positions_command(update, context)
    elif text == "🏷 Нікнейм":
        await nickname_command(update, context)
    elif text == "📈 Статус":
        await status_command(update, context)
    elif text == "❓ Допомога":
        await help_command(update, context)


async def update_pinned_positions(app):
    """Оновлює або створює закріплене повідомлення зі зведенням позицій."""
    for chat_id, trader_list in monitor.traders.items():
        if not trader_list:
            continue

        # Збираємо зведення по всіх трейдерах
        total_value = 0
        total_pnl = 0
        trader_lines = []
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

        for trader in trader_list:
            address = trader["address"]
            nick = monitor.get_nickname(chat_id, address)
            positions = await monitor.get_positions_report(address)

            val = sum(p.get("current_value", 0) for p in positions)
            pnl = sum(p.get("pnl", 0) for p in positions)
            invested = sum(p.get("invested", 0) for p in positions)
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            total_value += val
            total_pnl += pnl

            p_emoji = "🟢" if pnl >= 0 else "🔴"
            p_sign = "+" if pnl >= 0 else ""
            trader_lines.append(
                f"👤 *{nick}*\n"
                f"   💰 ${val:.2f}  {p_emoji} {p_sign}${pnl:.2f} ({p_sign}{pnl_pct:.1f}%)\n"
                f"   📌 Позицій: {len(positions)}"
            )

        overall_sign = "+" if total_pnl >= 0 else ""
        overall_emoji = "🟢" if total_pnl >= 0 else "🔴"

        pin_text = (
            f"📌 *Зведення позицій*\n"
            f"🕐 {now}\n\n"
            f"💼 Загальна вартість: *${total_value:.2f}*\n"
            f"{overall_emoji} Загальний PnL: *{overall_sign}${total_pnl:.2f}*\n\n"
            + "\n\n".join(trader_lines) +
            f"\n\n_Оновлюється щогодини_"
        )

        try:
            if chat_id in pinned_messages:
                # Редагуємо існуюче повідомлення
                try:
                    await app.bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=pinned_messages[chat_id],
                        text=pin_text,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Updated pinned message for {chat_id}")
                    continue
                except Exception:
                    pass  # Якщо не вдалось редагувати — створюємо нове

            # Надсилаємо нове і закріплюємо
            msg = await app.bot.send_message(
                chat_id=int(chat_id),
                text=pin_text,
                parse_mode="Markdown"
            )
            pinned_messages[chat_id] = msg.message_id
            await app.bot.pin_chat_message(
                chat_id=int(chat_id),
                message_id=msg.message_id,
                disable_notification=True
            )
            logger.info(f"Pinned new positions message for {chat_id}")

        except Exception as e:
            logger.error(f"Failed to update pinned message for {chat_id}: {e}")


async def run_monitor_loop(app):
    logger.info("Monitor loop started")
    last_report_time = 0

    while True:
        try:
            new_trades = await monitor.check_new_trades()
            for chat_id, trade in new_trades:
                await send_trade_notification(app.bot, chat_id, trade)

            now = asyncio.get_event_loop().time()
            if now - last_report_time >= HOURLY_INTERVAL:
                last_report_time = now
                logger.info("Hourly report + pinned update...")
                await update_pinned_positions(app)

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        await asyncio.sleep(30)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_command),
            MessageHandler(filters.Regex("^➕ Додати трейдера$"), add_command),
        ],
        states={
            WAITING_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)],
            WAITING_NICKNAME: [
                CommandHandler("skip", skip_nickname),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_nickname),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("nickname", nickname_command))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^(📋|📊|🏷|📈|❓)"),
        handle_keyboard_buttons
    ))

    async def post_init(application):
        asyncio.create_task(run_monitor_loop(application))

    app.post_init = post_init

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
