import logging
import os
import threading
from datetime import timezone
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
    get_event_counts,
    get_known_channel_id,
    init_db,
    record_event,
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
    except ValueError as exc:
        raise RuntimeError(
            f"Переменная {name} должна содержать целое число"
        ) from exc


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = env_int("ADMIN_CHAT_ID")
CHANNEL_ID = env_int("CHANNEL_ID")
GROUP_CHAT_ID = env_int("GROUP_CHAT_ID")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
PORT = int(os.getenv("PORT", "10000"))

if CHANNEL_USERNAME and not CHANNEL_USERNAME.startswith("@"):
    CHANNEL_USERNAME = f"@{CHANNEL_USERNAME}"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.end_headers()
        self.wfile.write(
            b"PROFITx Control Center is running"
        )

    def log_message(self, format: str, *args) -> None:
        return


def run_health_server() -> None:
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    logger.info(
        "Health server started on port %s",
        PORT,
    )

    server.serve_forever()


def is_member(
    status: str,
    is_member_flag: bool | None = None,
) -> bool:
    if status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    ):
        return True

    if status == ChatMemberStatus.RESTRICTED:
        return bool(is_member_flag)

    return False


def get_channel_reference() -> int | str | None:
    if CHANNEL_ID:
        return CHANNEL_ID

    if CHANNEL_USERNAME:
        return CHANNEL_USERNAME

    return get_known_channel_id()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    text = (
        "✅ PROFITx Analytics запущен.\n\n"
        f"Ваш Telegram ID: <code>{user.id}</code>\n\n"
        "Команды:\n"
        "/stats — полная статистика\n"
        "/today — события за последние 24 часа\n"
        "/id — показать ID пользователя и чата"
    )

    await update.effective_message.reply_html(text)


async def show_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    user = update.effective_user

    await update.effective_message.reply_text(
        f"Ваш Telegram ID: {user.id}\n"
        f"ID текущего чата: {chat.id}\n"
        f"Название чата: {chat.title or 'Личный чат'}\n"
        f"Тип чата: {chat.type}"
    )


async def get_member_count(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | str | None,
) -> str:
    if not chat_id:
        return "не указан"

    try:
        total = await context.bot.get_chat_member_count(
            chat_id=chat_id
        )
        return str(total)

    except Exception as exc:
        logger.exception(
            "Не удалось получить количество участников "
            "для чата %s: %s",
            chat_id,
            exc,
        )

        return "не удалось получить"


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        ADMIN_CHAT_ID
        and update.effective_user.id != ADMIN_CHAT_ID
    ):
        await update.effective_message.reply_text(
            "Эта команда доступна только владельцу."
        )
        return
[26.07.2026 7:03] 🄶 Gabriel: counts = get_event_counts(days=None)
    today_counts = get_event_counts(days=1)

    channel_ref = get_channel_reference()

    channel_total = await get_member_count(
        context,
        channel_ref,
    )

    group_total = await get_member_count(
        context,
        GROUP_CHAT_ID,
    )

    today_result = (
        today_counts["joined"]
        - today_counts["left"]
    )

    await update.effective_message.reply_text(
        "📊 PROFITx — статистика\n\n"

        "📢 Канал PROFITx\n"
        f"Подписчиков сейчас: {channel_total}\n\n"

        "💬 Закрытая группа CashFlow\n"
        f"Участников сейчас: {group_total}\n\n"

        "📅 События за последние 24 часа\n"
        f"➕ Вступили: {today_counts['joined']}\n"
        f"➖ Вышли: {today_counts['left']}\n"
        f"📈 Изменение: {today_result:+d}\n\n"

        "📚 С момента запуска бота\n"
        f"➕ Вступили: {counts['joined']}\n"
        f"➖ Вышли: {counts['left']}"
    )


async def today(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        ADMIN_CHAT_ID
        and update.effective_user.id != ADMIN_CHAT_ID
    ):
        await update.effective_message.reply_text(
            "Эта команда доступна только владельцу."
        )
        return

    counts = get_event_counts(days=1)

    result = (
        counts["joined"]
        - counts["left"]
    )

    await update.effective_message.reply_text(
        "📅 За последние 24 часа\n\n"
        f"➕ Вступили: {counts['joined']}\n"
        f"➖ Вышли: {counts['left']}\n"
        f"📈 Изменение: {result:+d}"
    )


async def on_chat_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    change = update.chat_member

    if change is None:
        return

    chat = change.chat
    user = change.new_chat_member.user
    old = change.old_chat_member
    new = change.new_chat_member

    old_member = is_member(
        old.status,
        getattr(old, "is_member", None),
    )

    new_member = is_member(
        new.status,
        getattr(new, "is_member", None),
    )

    if old_member == new_member:
        return

    event_type = (
        "joined"
        if new_member
        else "left"
    )

    username_text = (
        f"@{user.username}"
        if user.username
        else ""
    )

    full_name = (
        user.full_name
        or "Без имени"
    )

    invite_link = (
        change.invite_link.invite_link
        if change.invite_link
        else None
    )

    record_event(
        chat_id=chat.id,
        chat_title=chat.title or "",
        user_id=user.id,
        full_name=full_name,
        username=user.username,
        event_type=event_type,
        invite_link=invite_link,
        event_time=change.date.astimezone(
            timezone.utc
        ),
    )

    if chat.type == "channel":
        save_setting(
            "channel_id",
            str(chat.id),
        )

    if ADMIN_CHAT_ID:
        symbol = (
            "➕"
            if event_type == "joined"
            else "➖"
        )

        action = (
            "вступил(а)"
            if event_type == "joined"
            else "вышел(а)"
        )

        chat_type_text = (
            "канал"
            if chat.type == "channel"
            else "группу"
        )

        details = (
            f"{full_name} {username_text}"
        ).strip()

        message = (
            f"{symbol} {details} {action} "
            f"в {chat_type_text} "
            f"«{chat.title or chat.id}»."
        )

        if invite_link and event_type == "joined":
            message += (
                f"\n🔗 Ссылка: {invite_link}"
            )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=message,
            )

        except Exception as exc:
            logger.exception(
                "Не удалось отправить уведомление "
                "владельцу: %s",
                exc,
            )


async def on_my_chat_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
[26.07.2026 7:03] 🄶 Gabriel: ) -> None:
    change = update.my_chat_member

    if not change:
        return

    logger.info(
        "Статус бота изменён в чате "
        "%s (%s): %s -> %s",
        change.chat.title,
        change.chat.id,
        change.old_chat_member.status,
        change.new_chat_member.status,
    )

    if change.chat.type == "channel":
        save_setting(
            "channel_id",
            str(change.chat.id),
        )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задана переменная BOT_TOKEN"
        )

    init_db()

    threading.Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "id",
            show_id,
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today,
        )
    )

    application.add_handler(
        ChatMemberHandler(
            on_chat_member,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    application.add_handler(
        ChatMemberHandler(
            on_my_chat_member,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

    logger.info(
        "PROFITx bot is starting. "
        "Channel: %s | Group: %s",
        get_channel_reference(),
        GROUP_CHAT_ID,
    )

    application.run_polling(
        allowed_updates=[
            "message",
            "chat_member",
            "my_chat_member",
        ],
        drop_pending_updates=False,
    )


if name == "__main__":
    main()
