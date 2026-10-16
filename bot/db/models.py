from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    # None until onboarding is finished
    timezone: Mapped[str | None] = mapped_column(String(64))
    base_currency: Mapped[str | None] = mapped_column(String(3))
    remind_time: Mapped[time] = mapped_column(Time, default=time(10, 0))
    # Days before the charge date; 0 means "on the day"
    default_offsets: Mapped[list[int]] = mapped_column(JSON, default=lambda: [3, 0])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    period_unit: Mapped[str] = mapped_column(String(8))  # day / week / month / year
    period_count: Mapped[int] = mapped_column(Integer, default=1)
    # First paid charge; all later charge dates are derived from it to avoid drift
    anchor_date: Mapped[date] = mapped_column(Date)
    next_charge_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(10), default="active")  # trial / active / archived
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_end: Mapped[date | None] = mapped_column(Date)
    remind_offsets: Mapped[list[int] | None] = mapped_column(JSON)
    category: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="subscriptions")
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    charge_date: Mapped[date] = mapped_column(Date)
    # Price is copied so that history survives later price changes
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))

    subscription: Mapped[Subscription] = relationship(back_populates="payments")

    __table_args__ = (UniqueConstraint("subscription_id", "charge_date"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    charge_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))  # before_N / charged / due
    # pending -> sent; skipped when superseded or too old to be useful
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subscription: Mapped[Subscription] = relationship()

    # Guarantees each reminder is delivered at most once, even across restarts
    __table_args__ = (UniqueConstraint("subscription_id", "charge_date", "kind"),)


class FxRate(Base):
    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    # Stored as text: SQLite has no exact decimal type
    rate: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("base", "quote"),)
