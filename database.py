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
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


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


def get_event_counts(days: int | None) -> dict[str, int]:
    since = None
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)

    result = {"joined": 0, "left": 0}
    with SessionLocal() as session:
        stmt = select(MemberEvent.event_type, func.count(MemberEvent.id)).group_by(
            MemberEvent.event_type
        )
        if since:
            stmt = stmt.where(MemberEvent.event_time >= since)
        for event_type, count in session.execute(stmt):
            if event_type in result:
                result[event_type] = int(count)
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
        return int(setting.value) if setting else None
