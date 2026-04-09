from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import build_interbank_jwt, decode_interbank_jwt, new_nonce
from app.central_bank import get_bank_from_cache_or_central, get_exchange_rates
from app.models import Account, Transfer, ReplayNonce, User, BranchConfig, BankDirectoryEntry


settings = get_settings()


def make_user_id() -> str:
    return f"user-{uuid4()}"


def make_api_key() -> str:
    return f"pk_{uuid4().hex}{uuid4().hex}"


def generate_account_number(db: Session, bank_prefix: str) -> str:
    for _ in range(50):
        suffix = uuid4().hex[:5].upper()
        account_number = f"{bank_prefix}{suffix}"
        exists = db.query(Account).filter(Account.account_number == account_number).first()
        if not exists:
            return account_number
    raise RuntimeError("Failed to generate unique account number")


def to_decimal_amount(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal_rate(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def convert_amount(amount: Decimal, source_currency: str, target_currency: str) -> tuple[Decimal, Decimal | None, datetime | None]:
    if source_currency == target_currency:
        return amount, None, None

    rates_data = get_exchange_rates()
    rates = {k: to_decimal_rate(v) for k, v in rates_data["rates"].items()}
    base = rates_data["baseCurrency"]
    ts = datetime.fromisoformat(rates_data["timestamp"].replace("Z", "+00:00"))

    if source_currency == base:
        rate = rates[target_currency]
    elif target_currency == base:
        rate = Decimal("1") / rates[source_currency]
    else:
        rate = rates[target_currency] / rates[source_currency]

    converted = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return converted, rate, ts


def ensure_transfer_not_exists(db: Session, transfer_id: str) -> None:
    existing = db.query(Transfer).filter(Transfer.transfer_id == transfer_id).first()
    if not existing:
        return

    if existing.status == "pending":
        raise HTTPException(status_code=409, detail={"code": "TRANSFER_ALREADY_PENDING", "message": f"Transfer with ID '{transfer_id}' is already pending. Cannot submit duplicate transfer."})
    raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": f"A transfer with ID '{transfer_id}' already exists"})


def interbank_receive_url(address: str) -> str:
    if address.endswith("/api/v1"):
        return f"{address}/transfers/receive"
    return urljoin(address.rstrip("/") + "/", "api/v1/transfers/receive")


def account_lookup_url(address: str, account_number: str) -> str:
    if address.endswith("/api/v1"):
        return f"{address}/accounts/{account_number}"
    return urljoin(address.rstrip("/") + "/", f"api/v1/accounts/{account_number}")


def resolve_bank_id_from_prefix(db: Session, bank_prefix: str) -> str | None:
    bank = (
        db.query(BankDirectoryEntry)
        .filter(BankDirectoryEntry.bank_id.like(f"{bank_prefix}%"))
        .order_by(BankDirectoryEntry.bank_id.asc())
        .first()
    )
    return bank.bank_id if bank else None


def fetch_destination_currency(db: Session, destination_bank_id: str, destination_account: str) -> str | None:
    bank = get_bank_from_cache_or_central(db, destination_bank_id)
    if not bank:
        return None

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(account_lookup_url(bank.address, destination_account))
    except Exception:
        return None

    if response.status_code != 200:
        return None

    data = response.json()
    currency = data.get("currency")
    return currency if isinstance(currency, str) else None


def lock_and_get_account(db: Session, account_number: str) -> Account | None:
    return db.query(Account).filter(Account.account_number == account_number).with_for_update().first()


def send_outgoing_interbank_transfer(
    db: Session,
    cfg: BranchConfig,
    transfer: Transfer,
    destination_bank_id: str,
    converted_amount: Decimal,
) -> tuple[bool, str | None]:
    bank = get_bank_from_cache_or_central(db, destination_bank_id)
    if not bank:
        return False, "Destination bank not found"

    payload = {
        "transferId": transfer.transfer_id,
        "sourceAccount": transfer.source_account,
        "destinationAccount": transfer.destination_account,
        "amount": f"{converted_amount:.2f}",
        "sourceBankId": cfg.bank_id,
        "destinationBankId": destination_bank_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": new_nonce(),
    }

    token = build_interbank_jwt(payload, cfg.private_key)
    body = {"jwt": token}

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(interbank_receive_url(bank.address), json=body)
    except Exception:
        return False, "Destination bank is temporarily unavailable"

    if response.status_code in (200, 201):
        return True, None
    if response.status_code in (503, 500, 502, 504):
        return False, "Destination bank is temporarily unavailable"

    msg = response.text if response.text else "Interbank transfer failed"
    return False, msg


def schedule_pending(transfer: Transfer) -> None:
    now = datetime.now(timezone.utc)
    transfer.status = "pending"
    transfer.pending_since = now
    transfer.retry_count = 0
    transfer.next_retry_at = now + timedelta(minutes=1)


def check_timeout_and_refund(db: Session, transfer: Transfer) -> bool:
    if transfer.status != "pending" or not transfer.pending_since:
        return False

    elapsed = datetime.now(timezone.utc) - transfer.pending_since
    if elapsed.total_seconds() < settings.pending_timeout_seconds:
        return False

    source = lock_and_get_account(db, transfer.source_account)
    if source:
        source.balance = (source.balance + transfer.amount).quantize(Decimal("0.01"))

    transfer.status = "failed_timeout"
    transfer.error_message = "Transfer timed out after 4 hours. Funds refunded to source account."
    transfer.next_retry_at = None
    db.commit()
    return True


def verify_and_decode_interbank(db: Session, token: str) -> dict:
    unverified = token.split(".")
    if len(unverified) != 3:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Malformed JWT"})

    from jose.utils import base64url_decode
    import json

    payload = json.loads(base64url_decode(unverified[1].encode("utf-8")).decode("utf-8"))
    source_bank_id = payload.get("sourceBankId")
    nonce = payload.get("nonce")
    transfer_id = payload.get("transferId")

    bank = get_bank_from_cache_or_central(db, source_bank_id)
    if not bank:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Unknown source bank"})

    decoded = decode_interbank_jwt(token, bank.public_key)

    if nonce:
        existing = db.query(ReplayNonce).filter(ReplayNonce.issuer_bank_id == source_bank_id, ReplayNonce.nonce == nonce).first()
        if existing:
            raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": "Duplicate nonce detected"})
        db.add(ReplayNonce(issuer_bank_id=source_bank_id, nonce=nonce, transfer_id=transfer_id))
        db.commit()

    return decoded
