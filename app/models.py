from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, DateTime, Numeric, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    api_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    accounts = relationship("Account", back_populates="owner")


class Account(Base):
    __tablename__ = "accounts"

    account_number: Mapped[str] = mapped_column(String(8), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(50), ForeignKey("users.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="accounts")


class Transfer(Base):
    __tablename__ = "transfers"

    transfer_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_account: Mapped[str] = mapped_column(String(8), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    converted_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    rate_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="outgoing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_bank_id: Mapped[str | None] = mapped_column(String(6), nullable=True)
    destination_bank_id: Mapped[str | None] = mapped_column(String(6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class BankDirectoryEntry(Base):
    __tablename__ = "bank_directory_entries"

    bank_id: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BranchConfig(Base):
    __tablename__ = "branch_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bank_id: Mapped[str | None] = mapped_column(String(6), unique=True, nullable=True)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReplayNonce(Base):
    __tablename__ = "replay_nonces"
    __table_args__ = (UniqueConstraint("issuer_bank_id", "nonce", name="uq_nonce_issuer_nonce"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issuer_bank_id: Mapped[str] = mapped_column(String(6), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    transfer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
