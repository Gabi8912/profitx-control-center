import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

from database import (
    init_db,
    record_event,
    get_event_counts,
    get_known_channel_id,
    save_setting,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("profitx")


def env_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"{name} должен быть целым числом")


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = env_int("ADMIN_CHAT_ID")
CHANNEL_ID = env_int("CHANNEL_ID")
PORT = int(os.getenv("PORT", "10000"))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"PROFITx Control Center is running")

    def log_message(self, format, *args):
        return


def run_health_server() -> None:
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("Health server started on port %s", PORT)
    server.serve_forever()


def is_member(status: str, is_member_flag: bool | None = None) -> bool:
    if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return True
    if status == ChatMemberStatus.RESTRICTED:
        return bool(is_member_flag)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        "✅ PROFITx Channel Analytics запущен.\n\n"
        f"Ваш Telegram ID: <code>{user.id}</code>\n\n"
        "Команды:\n"
        "/stats — статистика вступлений и выходов\n"
        "/today — события за сегодня\n"
        "/id — показать ваш ID"
    )
    await update.effective_message.reply_html(text)


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"Ваш Telegram ID: {update.effective_user.id}"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_CHAT_ID and update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("Эта команда доступна владельцу.")
        return

    counts = get_event_counts(days=None)
    today = get_event_counts(days=1)
    channel_id = CHANNEL_ID or get_known_channel_id()

    total_text = "не удалось получить"
    if channel_id:
        try:
            total = await context.bot.get_chat_member_count(channel_id)
            total_text = str(total)
        except Exception as exc:
            logger.warning("Cannot get member count: %s", exc)

    await update.effective_message.reply_text(
        "📊 PROFITx — статистика\n\n"
        f"Подписчиков сейчас: {total_text}\n"
        f"Сегодня вступили: {today['joined']}\n"
        f"Сегодня вышли: {today['left']}\n"
        f"Итог сегодня: {today['joined'] - today['left']:+d}\n\n"
        f"С момента запуска бота:\n"
        f"Вступили: {counts['joined']}\n"
        f"Вышли: {counts['left']}"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_CHAT_ID and update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("Эта команда доступна владельцу.")
        return
    counts = get_event_counts(days=1)
    await update.effective_message.reply_text(
        "📅 За последние 24 часа\n\n"
        f"➕ Вступили: {counts['joined']}\n"
        f"➖ Вышли: {counts['left']}\n"
        f"📈 Изменение: {counts['joined'] - counts['left']:+d}"
    )


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.chat_member
    if change is None:
        return

    chat = change.chat
    user = change.new_chat_member.user
    old = change.old_chat_member
    new = change.new_chat_member

    old_member = is_member(old.status, getattr(old, "is_member", None))
    new_member = is_member(new.status, getattr(new, "is_member", None))

    if old_member == new_member:
        return

    event_type = "joined" if new_member else "left"
    username = f"@{user.username}" if user.username else ""
    full_name = user.full_name or "Без имени"
    invite_link = change.invite_link.invite_link if change.invite_link else None

    record_event(
        chat_id=chat.id,
        chat_title=chat.title or "",
        user_id=user.id,
        full_name=full_name,
        username=user.username,
        event_type=event_type,
        invite_link=invite_link,
        event_time=change.date.astimezone(timezone.utc),
    )
    save_setting("channel_id", str(chat.id))

    if ADMIN_CHAT_ID:
        symbol = "➕" if event_type == "joined" else "➖"
        action = "вступил(а)" if event_type == "joined" else "вышел(а)"
        details = f"{full_name} {username}".strip()
        message = f"{symbol} {details} {action} из канала «{chat.title}»."
        if invite_link and event_type == "joined":
            message += f"\n🔗 Ссылка: {invite_link}"
        try:
            await context.bot.send_message(ADMIN_CHAT_ID, message)
        except Exception as exc:
            logger.warning("Cannot notify admin: %s", exc)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if not change:
        return
    logger.info(
        "Bot status changed in %s (%s): %s -> %s",
        change.chat.title,
        change.chat.id,
        change.old_chat_member.status,
        change.new_chat_member.status,
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная BOT_TOKEN")

    init_db()

    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", show_id))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    logger.info("PROFITx bot is starting")
    application.run_polling(
        allowed_updates=["message", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
