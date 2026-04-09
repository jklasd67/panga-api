from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ErrorResponse(BaseModel):
    code: str
    message: str


class UserRegistrationRequest(BaseModel):
    fullName: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None


class UserRegistrationResponse(BaseModel):
    userId: str
    fullName: str
    email: EmailStr | None = None
    createdAt: datetime
    apiKey: str


class UserInfoResponse(BaseModel):
    userId: str
    fullName: str
    email: EmailStr | None
    createdAt: datetime


class AccountCreationRequest(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class AccountCreationResponse(BaseModel):
    accountNumber: str
    ownerId: str
    currency: str
    balance: str
    createdAt: datetime


class AccountLookupResponse(BaseModel):
    accountNumber: str
    ownerName: str
    currency: str


class TransferRequest(BaseModel):
    transferId: UUID
    sourceAccount: str = Field(pattern=r"^[A-Z0-9]{8}$")
    destinationAccount: str = Field(pattern=r"^[A-Z0-9]{8}$")
    amount: str = Field(pattern=r"^\d+\.\d{2}$")


class TransferResponse(BaseModel):
    transferId: UUID
    status: Literal["completed", "failed", "pending", "failed_timeout"]
    sourceAccount: str
    destinationAccount: str
    amount: str
    convertedAmount: str | None = None
    exchangeRate: str | None = None
    rateCapturedAt: datetime | None = None
    pendingSince: datetime | None = None
    nextRetryAt: datetime | None = None
    retryCount: int | None = None
    timestamp: datetime
    errorMessage: str | None = None


class TransferHistoryResponse(BaseModel):
    transfers: list[TransferResponse]


class InterBankTransferRequest(BaseModel):
    jwt: str


class InterBankTransferResponse(BaseModel):
    transferId: UUID
    status: Literal["completed", "failed"]
    destinationAccount: str
    amount: str
    timestamp: datetime


class BankRegistrationRequest(BaseModel):
    name: str
    address: str
    publicKey: str


class HeartbeatRequest(BaseModel):
    timestamp: datetime


class BankDirectoryEntryModel(BaseModel):
    bankId: str
    name: str
    address: str
    publicKey: str
    lastHeartbeat: datetime
    status: str


class ExchangeRateResponse(BaseModel):
    baseCurrency: str
    rates: dict[str, str]
    timestamp: datetime


def decimal_to_amount(value: Decimal) -> str:
    return f"{value:.2f}"


def decimal_to_rate(value: Decimal) -> str:
    return f"{value:.6f}"
