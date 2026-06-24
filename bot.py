import asyncio
import logging
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from monitor import PolymarketMonitor, MetarMonitor

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_ADDRESS = 1
WAITING_NICKNAME = 2
WAITING_TYPE = 3
WAITING_METAR = 4

HOURLY_INTERVAL = 3600
DAILY_INTERVAL = 86400

monitor = PolymarketMonitor()
metar_monitor = MetarMonitor()
pinned_messages = {}


def main_keyboard():
    keyboard = [
        [KeyboardButton("💼 Мій портфель"), KeyboardButton("👥 Трейдери")],
        [KeyboardButton("➕ Додати"), KeyboardButton("📋 Список")],
        [KeyboardButton("✈️ Станції"), KeyboardButton("📈 Статус")],
        [KeyboardButton("❓ Допомога")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Polymarket Trade Monitor*\n\n"
        "Відстежую угоди та позиції на Polymarket.\n\n"
        "💼 *Мій портфель* — твої особисті позиції з PnL\n"
        "👥 *Трейдери* — позиції тих за ким стежиш\n\n"
        "Починай з ➕ Додати"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Довідка*\n\n"
        "💼 *Мій портфель* — щогодинний звіт по своїх позиціях\n"
        "👥 *Трейдери* — позиції відстежуваних по запиту\n"
        "➕ *Додати* — додати свій або чужий акаунт\n"
        "📋 *Список* — всі акаунти\n\n"
        "🔔 *Сповіщення:*\n"
        "▸ Нова угода — одразу (всі акаунти)\n"
        "▸ Мій портфель — щогодини автоматично\n"
        "▸ Трейдери — раз на добу або по запиту"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("💼 Мій акаунт"), KeyboardButton("👁 Відстежувати трейдера")],
        [KeyboardButton("❌ Скасувати")],
    ], resize_keyboard=True)
    await update.message.reply_text(
        "Що хочеш додати?",
        reply_markup=keyboard
    )
    return WAITING_TYPE


async def receive_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Скасувати":
        await update.message.reply_text("❌ Скасовано.", reply_markup=main_keyboard())
        return ConversationHandler.END

    if text == "💼 Мій акаунт":
        context.user_data["add_type"] = "own"
        label = "свого акаунту"
    else:
        context.user_data["add_type"] = "watch"
        label = "трейдера якого хочеш відстежувати"

    await update.message.reply_text(
        f"📝 Введи адресу {label}:\n\n"
        f"Приклад: `0xd5B86E84Be3bC0BD2D5A3D5f9b3b5a8b3c9e0f1a`\n\n"
        f"або /cancel для скасування",
        parse_mode="Markdown"
    )
    return WAITING_ADDRESS



async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = query.data

    if data.startswith("remove:"):
        address = data.split(":", 1)[1]
        monitor.remove_trader(chat_id, address)
        nick = address[:10] + "..." + address[-6:]
        await query.edit_message_text(f"✅ Видалено: `{nick}`", parse_mode="Markdown")

    elif data.startswith("setnick:"):
        address = data.split(":", 1)[1]
        context.user_data["nick_address"] = address
        await query.edit_message_text(
            f"🏷 Введи нікнейм для `{address[:10]}...{address[-6:]}`:",
            parse_mode="Markdown"
        )

    elif data == "show_all_positions":
        address = context.user_data.get("full_positions_address", "")
        positions = context.user_data.get("full_positions_data", [])
        if positions and address:
            bot = query.get_bot() if hasattr(query, 'get_bot') else update.get_bot()
            await send_positions_report(bot, chat_id, address, positions, show_all=True)


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip().lower()

    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text(
            "❌ Невірний формат. Адреса починається з `0x` і має 42 символи.\nСпробуй ще раз:",
            parse_mode="Markdown"
        )
        return WAITING_ADDRESS

    context.user_data["new_address"] = address
    is_own = context.user_data.get("add_type", "watch") == "own"
    context.user_data["is_own"] = is_own

    label = "свій акаунт" if is_own else "трейдера"
    await update.message.reply_text(
        f"✅ Адресу отримано!\n\n"
        f"Дай нікнейм для цього {label}?\n"
        f"Наприклад: {'Мій акаунт' if is_own else 'Кит №1'}\n\n"
        f"Або /skip щоб пропустити",
        parse_mode="Markdown"
    )
    return WAITING_NICKNAME


async def receive_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    address = context.user_data.get("new_address", "")
    is_own = context.user_data.get("is_own", False)
    nickname = update.message.text.strip()

    monitor.add_trader(chat_id, address, nickname=nickname, is_own=is_own)

    type_label = "💼 Твій акаунт" if is_own else "👁 Відстежуваний трейдер"
    report_label = "Щогодинний звіт по позиціях" if is_own else "Звіт по запиту або раз на добу"

    await update.message.reply_text(
        f"✅ Додано!\n\n"
        f"{type_label}\n"
        f"🏷 {nickname}\n"
        f"📍 `{address[:10]}...{address[-6:]}`\n\n"
        f"🔔 Нові угоди — одразу\n"
        f"📊 {report_label}",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def skip_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    address = context.user_data.get("new_address", "")
    is_own = context.user_data.get("is_own", False)

    monitor.add_trader(chat_id, address, is_own=is_own)
    type_label = "💼 Твій акаунт" if is_own else "👁 Відстежуваний трейдер"

    await update.message.reply_text(
        f"✅ Додано!\n\n"
        f"{type_label}\n"
        f"📍 `{address[:10]}...{address[-6:]}`\n"
        f"🟢 Моніторинг активний",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def my_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка 'Мій портфель' — показує власні акаунти."""
    chat_id = str(update.effective_chat.id)
    own = monitor.get_own_accounts(chat_id)

    if not own:
        await update.message.reply_text(
            "💼 Свого акаунту ще немає.\n\nДодай через ➕ Додати → 💼 Мій акаунт",
            reply_markup=main_keyboard()
        )
        return

    msg = await update.message.reply_text("⏳ Завантажую портфель...")
    bot = update.get_bot()
    for trader in own:
        address = trader["address"]
        positions, pnl_stats = await asyncio.gather(
            monitor.get_positions_report(address),
            monitor.get_pnl_stats(address)
        )
        await send_positions_report(bot, chat_id, address, positions, context=context, pnl_stats=pnl_stats)
    try:
        await msg.delete()
    except Exception:
        pass


async def watched_traders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка 'Трейдери' — показує відстежуваних."""
    chat_id = str(update.effective_chat.id)
    watched = monitor.get_watched_traders(chat_id)

    if not watched:
        await update.message.reply_text(
            "👥 Ще нікого не відстежуєш.\n\nДодай через ➕ Додати → 👁 Відстежувати трейдера",
            reply_markup=main_keyboard()
        )
        return

    # Показуємо список з кнопками для перегляду позицій
    keyboard = []
    for t in watched:
        nick = t.get("nickname") or f"{t['address'][:10]}...{t['address'][-6:]}"
        keyboard.append([InlineKeyboardButton(
            f"📊 {nick}",
            callback_data=f"viewpos:{t['address']}"
        )])

    await update.message.reply_text(
        "👥 *Відстежувані трейдери*\n\nОбери чиї позиції показати:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    own = monitor.get_own_accounts(chat_id)
    watched = monitor.get_watched_traders(chat_id)

    if not own and not watched:
        await update.message.reply_text("📋 Список порожній. Додай через ➕", reply_markup=main_keyboard())
        return

    lines = ["📋 *Всі акаунти:*\n"]

    if own:
        lines.append("💼 *Мої акаунти:*")
        for t in own:
            nick = t.get("nickname", "")
            addr = f"`{t['address'][:10]}...{t['address'][-6:]}`"
            lines.append(f"  • {addr}" + (f" — *{nick}*" if nick else ""))

    if watched:
        lines.append("\n👥 *Відстежувані:*")
        for t in watched:
            nick = t.get("nickname", "")
            addr = f"`{t['address'][:10]}...{t['address'][-6:]}`"
            lines.append(f"  • {addr}" + (f" — *{nick}*" if nick else ""))

    # Кнопка видалення
    keyboard = []
    for t in own + watched:
        nick = t.get("nickname") or f"{t['address'][:10]}...{t['address'][-6:]}"
        icon = "💼" if t.get("is_own") else "👁"
        keyboard.append([InlineKeyboardButton(f"🗑 {icon} {nick}", callback_data=f"remove:{t['address']}")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    own = monitor.get_own_accounts(chat_id)
    watched = monitor.get_watched_traders(chat_id)

    await update.message.reply_text(
        f"📈 *Статус*\n\n"
        f"🟢 Бот активний\n"
        f"💼 Моїх акаунтів: *{len(own)}*\n"
        f"👥 Відстежуваних: *{len(watched)}*\n\n"
        f"⏱ Перевірка угод: кожні 30 сек\n"
        f"💼 Мій портфель: щогодини\n"
        f"👥 Трейдери: раз на добу",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def send_positions_report(bot, chat_id: str, address: str, positions: list,
                                 show_all: bool = False, context=None, pnl_stats: dict = None):
    nick = monitor.get_nickname(chat_id, address)
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    MAX_SHOW = 5

    if not positions:
        text = f"📊 *{nick}*\n🕐 {now}\n\n— Відкритих позицій немає"
        if pnl_stats:
            m_pnl = pnl_stats["pnl_month"]
            a_pnl = pnl_stats["pnl_alltime"]
            m_sign = "+" if m_pnl >= 0 else ""
            a_sign = "+" if a_pnl >= 0 else ""
            m_emoji = "🟢" if m_pnl >= 0 else "🔴"
            a_emoji = "🟢" if a_pnl >= 0 else "🔴"
            text += (
                f"\n\n📊 *Profit/Loss:*\n"
                f"{m_emoji} {pnl_stats['month_name']}: *{m_sign}${m_pnl:.2f}*\n"
                f"{a_emoji} За весь час: *{a_sign}${a_pnl:.2f}*"
            )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        return

    total_value = sum(p.get("current_value", 0) for p in positions)
    total_pnl = sum(p.get("pnl", 0) for p in positions)
    total_invested = sum(p.get("invested", 0) for p in positions)
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    pnl_sign = "+" if total_pnl >= 0 else ""

    lines = [
        f"📊 *{nick}*",
        f"🕐 {now}",
        f"",
        f"💼 {len(positions)} позицій  |  💰 ${total_value:.2f}",
        f"{pnl_emoji} PnL відкритих: *{pnl_sign}${total_pnl:.2f}* ({pnl_sign}{total_pnl_pct:.1f}%)",
    ]

    if pnl_stats:
        m_pnl = pnl_stats["pnl_month"]
        a_pnl = pnl_stats["pnl_alltime"]
        m_sign = "+" if m_pnl >= 0 else ""
        a_sign = "+" if a_pnl >= 0 else ""
        m_color = "🟢" if m_pnl >= 0 else "🔴"
        a_color = "🟢" if a_pnl >= 0 else "🔴"
        total_balance = pnl_stats.get("total_balance", 0)
        usdc_balance = pnl_stats.get("usdc_balance", 0)
        open_value = pnl_stats.get("open_value", 0)
        if total_balance > 0:
            lines.append(f"")
            lines.append(f"💼 Баланс: *${total_balance:.2f}*")
            lines.append(f"   💵 Вільно: ${usdc_balance:.2f}  |  📊 Позиції: ${open_value:.2f}")
        lines.append(f"")
        lines.append(f"📊 *Profit/Loss:*")
        lines.append(f"{m_color} {pnl_stats['month_name']}: *{m_sign}${m_pnl:.2f}*")
        lines.append(f"{a_color} За весь час: *{a_sign}${a_pnl:.2f}*")

    lines += [
        f"",
        f"─────────────────",
    ]

    show_list = positions if show_all else positions[:MAX_SHOW]
    for i, pos in enumerate(show_list, 1):
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

        outcome_icon = "✅" if outcome.lower() == "yes" else "❌"
        pnl_icon = "📈" if pnl >= 0 else "📉"
        pnl_color = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"\n*{i}. {title}*")
        lines.append(f"   {outcome_icon} {outcome}  {avg_price:.3f} → {cur_price:.3f}")
        lines.append(f"   💵 ${cur_val:.2f}  {pnl_color} {pnl_icon} {p_sign}${pnl:.2f} ({p_sign}{pnl_pct:.1f}%)")
        if market_url:
            lines.append(f"   [🔗 Відкрити]({market_url})")

    text = "\n".join(lines)
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
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    except Exception as e:
        logger.error(f"Failed to send positions report: {e}")


async def send_trade_notification(bot, chat_id: str, trade: dict):
    market_url = trade.get("market_url", "")
    market_title = trade.get("market_title", "Невідомий ринок")
    outcome = trade.get("outcome", "")
    usd_value = trade.get("usd_value", 0)
    trader = trade.get("trader_address", "")
    timestamp = trade.get("timestamp", "")
    event_type = trade.get("event_type", "open")
    nick = monitor.get_nickname(chat_id, trader)

    # Визначаємо чи це свій акаунт
    is_own = any(
        t.get("is_own") and t["address"] == trader.lower()
        for t in monitor.get_traders(chat_id)
    )
    account_label = "💼 Мій акаунт" if is_own else f"👁 {nick}"

    if event_type == "close":
        avg_price = trade.get("avg_price", 0)
        size = trade.get("size", 0)
        text = (
            f"🔕 *Позицію закрито!*\n\n"
            f"{account_label}\n\n"
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
        side_emoji = "🟢" if side == "BUY" else "🔴"
        side_text = "КУПІВЛЯ" if side == "BUY" else "ПРОДАЖ"
        text = (
            f"🔔 *Нова угода!*\n\n"
            f"{account_label}\n\n"
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
        logger.error(f"Failed to send notification: {e}")


async def handle_view_positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопки перегляду позицій трейдера."""
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = query.data

    if data.startswith("viewpos:"):
        address = data.split(":", 1)[1]
        await query.edit_message_text("⏳ Завантажую позиції...")
        positions = await monitor.get_positions_report(address)
        bot = update.get_bot()
        await send_positions_report(bot, chat_id, address, positions, context=context)


async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💼 Мій портфель":
        await my_portfolio(update, context)
    elif text == "👥 Трейдери":
        await watched_traders(update, context)
    elif text == "➕ Додати":
        await add_command(update, context)
        return WAITING_ADDRESS
    elif text == "📋 Список":
        await list_command(update, context)
    elif text == "📈 Статус":
        await status_command(update, context)
    elif text == "✈️ Станції":
        await metar_stations_command(update, context)
    elif text == "❓ Допомога":
        await help_command(update, context)



async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує сирі типи транзакцій з API для дебагу PnL."""
    chat_id = str(update.effective_chat.id)
    own = monitor.get_own_accounts(chat_id)
    if not own:
        await update.message.reply_text("Немає свого акаунту.")
        return

    address = own[0]["address"]
    await update.message.reply_text("⏳ Завантажую дані...")

    import aiohttp
    async with aiohttp.ClientSession() as session:
        trades = await monitor.fetch_all_activity(address, session, limit=50)

    # Рахуємо унікальні типи
    types = {}
    for t in trades:
        tp = t.get("type", t.get("side", "UNKNOWN")).upper()
        usd = float(t.get("usdcSize", t.get("amount", 0)))
        if tp not in types:
            types[tp] = {"count": 0, "total": 0}
        types[tp]["count"] += 1
        types[tp]["total"] += usd

    lines = [f"🔍 *Типи транзакцій (останні 50):*\n"]
    for tp, data in sorted(types.items()):
        lines.append(f"`{tp}` — {data['count']} шт, ${data['total']:.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def metar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = query.data

    if data == "metar_add":
        await query.edit_message_text(
            "✈️ Введи код станції в чат (наприклад `KSEA` або кілька через пробіл `KSEA ZUUU`):",
            parse_mode="Markdown"
        )
        context.user_data["metar_adding"] = True

    elif data.startswith("metar_remove:"):
        code = data.split(":", 1)[1]
        metar_monitor.remove_station(chat_id, code)
        await query.edit_message_text(f"✅ Станцію `{code}` видалено.", parse_mode="Markdown")

    elif data.startswith("metar_info:"):
        code = data.split(":", 1)[1]
        metar_data = await metar_monitor.fetch_metar(code)
        if metar_data:
            temp_f = metar_data["temp_f"]
            temp_c = metar_data["temp_c"]
            time_str = metar_data["time"]
            await query.edit_message_text(
                f"✈️ *{code}*\n🌡 {temp_f:.1f}°F ({temp_c:.1f}°C)\n🕐 {time_str}",
                parse_mode="Markdown"
            )
        else:
            await query.answer("Немає даних для цієї станції")


async def metar_stations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує список METAR станцій і кнопки управління."""
    chat_id = str(update.effective_chat.id)
    stations = metar_monitor.get_stations(chat_id)

    keyboard = []
    for s in stations:
        code = s["code"]
        last_time = s.get("last_metar_time", "—")
        keyboard.append([
            InlineKeyboardButton(f"✈️ {code} | {last_time}", callback_data=f"metar_info:{code}"),
            InlineKeyboardButton("🗑", callback_data=f"metar_remove:{code}")
        ])

    active = len(stations)
    text = (
        f"✈️ *METAR Станції*\n\n"
        f"Активних: *{active}*\n"
        f"Сповіщення при кожному новому оновленні METAR.\n\n"
        f"Щоб додати — введи код(и) станції\n"
        f"Наприклад: `MMMX` або `MMMX ZUUU KSEA`"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )
    return WAITING_METAR


async def metar_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Починає додавання METAR станції."""
    await update.message.reply_text(
        "✈️ Введи код аеропорту (ICAO):\n\n"
        "Приклади: `KSEA`, `ZUUU`, `ZGSZ`, `KATL`\n\n"
        "Можна додати кілька через пробіл: `KSEA ZUUU ZGSZ`\n\n"
        "або /cancel для скасування",
        parse_mode="Markdown"
    )
    return WAITING_METAR


async def metar_receive_stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отримує і додає METAR станції."""
    chat_id = str(update.effective_chat.id)
    raw = update.message.text.strip().upper()
    codes = [c.strip() for c in raw.split() if c.strip()]

    if not codes:
        await update.message.reply_text("❌ Введи хоча б один код станції.")
        return WAITING_METAR

    msg = await update.message.reply_text("⏳ Перевіряю станції...")
    added = []
    failed = []

    for code in codes:
        if len(code) < 3 or len(code) > 5 or not code.isalpha():
            failed.append(f"{code} (невірний формат)")
            continue

        data = await metar_monitor.fetch_metar(code)
        if data is None:
            failed.append(f"{code} (не знайдено)")
            continue

        result = metar_monitor.add_station(chat_id, code)
        if result == "exists":
            added.append(f"{code} (вже є)")
        else:
            temp_f = data["temp_f"]
            temp_c = data["temp_c"]
            added.append(f"{code} — {temp_f:.1f}°F ({temp_c:.1f}°C)")

    lines = ["✅ *Результат:*\n"]
    if added:
        lines.append("*Додано:*")
        for a in added:
            lines.append(f"  ✈️ {a}")
    if failed:
        lines.append("\n*Не знайдено:*")
        for f in failed:
            lines.append(f"  ❌ {f}")

    try:
        await msg.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def send_metar_notification(bot, chat_id: str, station: str, data: dict):
    """Надсилає сповіщення про нове METAR оновлення."""
    temp_f = data["temp_f"]
    temp_c = data["temp_c"]
    time_str = data["time"]
    raw = data.get("raw", "")

    text = (
        f"✈️ *{station}* — нове METAR оновлення\n\n"
        f"🌡 *{temp_f:.1f}°F* ({temp_c:.1f}°C)\n"
        f"🕐 {time_str}\n\n"
        f"`{raw[:80]}`"
    )

    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send METAR notification: {e}")


async def run_monitor_loop(app):
    logger.info("Monitor loop started")
    last_hourly = 0
    last_daily = 0

    while True:
        try:
            # Нові угоди — кожні 30 сек
            new_trades = await monitor.check_new_trades()
            for chat_id, trade in new_trades:
                await send_trade_notification(app.bot, chat_id, trade)

            now = asyncio.get_event_loop().time()

            # Мій портфель — щогодини
            if now - last_hourly >= HOURLY_INTERVAL:
                last_hourly = now
                logger.info("Hourly: sending own accounts report...")
                for chat_id, trader_list in monitor.traders.items():
                    for trader in trader_list:
                        if trader.get("is_own"):
                            positions = await monitor.get_positions_report(trader["address"])
                            await send_positions_report(app.bot, chat_id, trader["address"], positions)

            # Відстежувані трейдери — раз на добу
            if now - last_daily >= DAILY_INTERVAL:
                last_daily = now
                logger.info("Daily: sending watched traders report...")
                for chat_id, trader_list in monitor.traders.items():
                    for trader in trader_list:
                        if not trader.get("is_own"):
                            positions = await monitor.get_positions_report(trader["address"])
                            await send_positions_report(app.bot, chat_id, trader["address"], positions)

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        # METAR перевірка кожну хвилину
        if int(asyncio.get_event_loop().time()) % 60 < 30:
            try:
                metar_updates = await metar_monitor.check_updates()
                for chat_id, station, data in metar_updates:
                    await send_metar_notification(app.bot, chat_id, station, data)
            except Exception as e:
                logger.error(f"METAR check error: {e}")

        await asyncio.sleep(30)


async def run_web_server():
    """Веб-сервер щоб Render не таймаутив."""
    async def health(request):
        return web.Response(text="OK")
    webapp = web.Application()
    webapp.router.add_get("/", health)
    webapp.router.add_get("/health", health)
    runner = web.AppRunner(webapp)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_command),
            MessageHandler(filters.Regex("^➕ Додати$"), add_command),
        ],
        states={
            WAITING_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_type)],
            WAITING_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)],
            WAITING_NICKNAME: [
                CommandHandler("skip", skip_nickname),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_nickname),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    metar_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addstation", metar_add_start),
            CommandHandler("stations", metar_stations_command),
            MessageHandler(filters.Regex("^✈️ Станції$"), metar_stations_command),
        ],
        states={
            WAITING_METAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, metar_receive_stations)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stations", metar_stations_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(conv_handler)
    app.add_handler(metar_conv_handler)
    app.add_handler(CallbackQueryHandler(handle_view_positions_callback, pattern="^viewpos:"))
    app.add_handler(CallbackQueryHandler(metar_callback, pattern="^metar_"))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_keyboard_buttons
    ))

    async def post_init(application):
        await run_web_server()
        asyncio.create_task(run_monitor_loop(application))

    app.post_init = post_init
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
