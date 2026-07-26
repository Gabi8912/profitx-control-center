import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///profitx.db").strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg://",
        1,
    )
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1,
    )

connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


class MemberEvent(Base):
    __tablename__ = "member_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_title: Mapped[str] = mapped_column(String(255), default="")
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)
    invite_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def record_event(
    chat_id: int,
    chat_title: str,
    user_id: int,
    full_name: str,
    username: str | None,
    event_type: str,
    invite_link: str | None,
    event_time: datetime,
) -> None:
    with SessionLocal() as session:
        session.add(
            MemberEvent(
                chat_id=chat_id,
                chat_title=chat_title,
                user_id=user_id,
                full_name=full_name,
                username=username,
                event_type=event_type,
                invite_link=invite_link,
                event_time=event_time,
            )
        )
        session.commit()


def get_event_counts(
    days: int | None,
    chat_id: int | None = None,
) -> dict[str, int]:
    since = None
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)

    result = {"joined": 0, "left": 0}

    with SessionLocal() as session:
        stmt = select(
            MemberEvent.event_type,
            func.count(MemberEvent.id),
        ).group_by(MemberEvent.event_type)

        if since is not None:
            stmt = stmt.where(MemberEvent.event_time >= since)
        if chat_id is not None:
            stmt = stmt.where(MemberEvent.chat_id == chat_id)

        for event_type, count in session.execute(stmt):
            if event_type in result:
                result[event_type] = int(count)

    return result


def get_last_join_invite_link(chat_id: int, user_id: int) -> str | None:
    """Return the most recent known invite link used by this user in this chat."""
    with SessionLocal() as session:
        stmt = (
            select(MemberEvent.invite_link)
            .where(
                MemberEvent.chat_id == chat_id,
                MemberEvent.user_id == user_id,
                MemberEvent.event_type == "joined",
                MemberEvent.invite_link.is_not(None),
            )
            .order_by(MemberEvent.event_time.desc(), MemberEvent.id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()


def get_source_stats(
    chat_id: int | None,
    source_links: dict[str, str],
    days: int | None = None,
) -> dict[str, dict[str, int]]:
    """
    Attribute joins and leaves to sources.

    A leave event normally has no invite link, so attribution is restored from
    the user's most recent join event. `active` is calculated from each user's
    latest membership state recorded by the bot.
    """
    keys = [*source_links.keys(), "other"]
    result = {
        key: {"joined": 0, "left": 0, "active": 0}
        for key in keys
    }

    if chat_id is None:
        return result

    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days is not None
        else None
    )

    link_to_source = {
        link.strip(): source
        for source, link in source_links.items()
        if link and link.strip()
    }

    def classify(invite_link: str | None) -> str:
        if invite_link:
            return link_to_source.get(invite_link.strip(), "other")
        return "other"

    with SessionLocal() as session:
        stmt = (
            select(MemberEvent)
            .where(MemberEvent.chat_id == chat_id)
            .order_by(MemberEvent.event_time.asc(), MemberEvent.id.asc())
        )
        events = list(session.scalars(stmt))

    user_source: dict[int, str] = {}
    user_active: dict[int, bool] = {}

    for event in events:
        event_source = user_source.get(event.user_id, "other")

        if event.event_type == "joined":
            event_source = classify(event.invite_link)
            user_source[event.user_id] = event_source
            user_active[event.user_id] = True
        elif event.event_type == "left":
            user_active[event.user_id] = False
        else:
            continue

        event_time = event.event_time
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        in_period = since is None or event_time >= since
        if in_period:
            result[event_source][event.event_type] += 1

    for user_id, active in user_active.items():
        if active:
            result[user_source.get(user_id, "other")]["active"] += 1

    return result


def save_setting(key: str, value: str) -> None:
    with SessionLocal() as session:
        setting = session.get(Setting, key)
        if setting:
            setting.value = value
        else:
            session.add(Setting(key=key, value=value))
        session.commit()


def get_known_channel_id() -> int | None:
    with SessionLocal() as session:
        setting = session.get(Setting, "channel_id")
        if not setting:
            return None
        return int(setting.value)
