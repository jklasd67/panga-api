from datetime import datetime, timezone
from decimal import Decimal
import logging
import threading
import time
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.central_bank import register_branch_if_needed, sync_directory
from app.config import get_settings
from app.crypto import ensure_keypair
from app.db import Base, SessionLocal, engine, get_db
from app.models import Account, Transfer, User, BranchConfig
from app.schemas import (
    AccountCreationRequest,
    AccountCreationResponse,
    AccountLookupResponse,
    ErrorResponse,
    TransferHistoryResponse,
    InterBankTransferRequest,
    InterBankTransferResponse,
    TransferRequest,
    TransferResponse,
    UserInfoResponse,
    UserRegistrationRequest,
    UserRegistrationResponse,
)
from app.service import (
    check_timeout_and_refund,
    convert_amount,
    ensure_transfer_not_exists,
    fetch_destination_currency,
    generate_account_number,
    lock_and_get_account,
    make_api_key,
    make_user_id,
    resolve_bank_id_from_prefix,
    schedule_pending,
    send_outgoing_interbank_transfer,
    to_decimal_amount,
    verify_and_decode_interbank,
)
from app.worker import process_pending_transfers
from app.central_bank import send_heartbeat, sync_directory


settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0")
logger = logging.getLogger("pangaapi")
maintenance_started = False


def maintenance_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            send_heartbeat(db)
            try:
                sync_directory(db)
            except Exception as exc:
                logger.warning("Directory sync skipped: %s", exc)
            process_pending_transfers()
        except Exception as exc:
            logger.warning("Maintenance loop error: %s", exc)
        finally:
            db.close()
        time.sleep(10)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    private_key, public_key = ensure_keypair()
    db = SessionLocal()
    try:
        try:
            cfg = register_branch_if_needed(db, public_key_pem=public_key, private_key_pem=private_key)
            if cfg.bank_id:
                sync_directory(db)
        except Exception as exc:
            logger.warning("Startup central bank sync skipped: %s", exc)
            cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
            if cfg and not cfg.bank_id:
                cfg.bank_id = settings.fallback_bank_id
                db.commit()
    finally:
        db.close()

    global maintenance_started
    if not maintenance_started:
        maintenance_started = True
        thread = threading.Thread(target=maintenance_loop, daemon=True)
        thread.start()


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"code": "ERROR", "message": str(exc.detail)})


@app.post("/api/v1/users", response_model=UserRegistrationResponse, status_code=201, responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def register_user(payload: UserRegistrationRequest, db: Session = Depends(get_db)):
    existing = None
    if payload.email:
        existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "A user with this email address is already registered"})

    user = User(
        id=make_user_id(),
        full_name=payload.fullName,
        email=payload.email,
        api_key=make_api_key(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return UserRegistrationResponse(
        userId=user.id,
        fullName=user.full_name,
        email=user.email,
        createdAt=user.created_at,
        apiKey=user.api_key,
    )


@app.get("/api/v1/users/{userId}", response_model=UserInfoResponse, responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def get_user(userId: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.id != userId:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot access another user"})

    user = db.query(User).filter(User.id == userId).first()
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{userId}' not found"})

    return UserInfoResponse(userId=user.id, fullName=user.full_name, email=user.email, createdAt=user.created_at)


@app.post("/api/v1/users/{userId}/accounts", response_model=AccountCreationResponse, status_code=201, responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def create_account(userId: str, payload: AccountCreationRequest, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    currency = payload.currency.upper()
    supported = {c.strip().upper() for c in settings.supported_currencies.split(",") if c.strip()}
    if currency not in supported:
        raise HTTPException(status_code=400, detail={"code": "UNSUPPORTED_CURRENCY", "message": f"Currency '{currency}' is not supported by this bank"})

    if current.id != userId:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot create account for another user"})

    user = db.query(User).filter(User.id == userId).first()
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{userId}' not found"})

    cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
    if not cfg or not cfg.bank_id:
        raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": "Branch is not registered with central bank"})

    account_number = generate_account_number(db, cfg.bank_id[:3])
    account = Account(account_number=account_number, owner_id=user.id, currency=currency, balance=Decimal("0.00"))
    db.add(account)
    db.commit()
    db.refresh(account)

    return AccountCreationResponse(
        accountNumber=account.account_number,
        ownerId=account.owner_id,
        currency=account.currency,
        balance=f"{account.balance:.2f}",
        createdAt=account.created_at,
    )


@app.get("/api/v1/accounts/{accountNumber}", response_model=AccountLookupResponse, responses={404: {"model": ErrorResponse}})
def lookup_account(accountNumber: str, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.account_number == accountNumber.upper()).first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account with number '{accountNumber}' not found"})

    return AccountLookupResponse(accountNumber=account.account_number, ownerName=account.owner.full_name, currency=account.currency)


@app.post("/api/v1/transfers", response_model=TransferResponse, status_code=201, responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def initiate_transfer(payload: TransferRequest, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transfer_id = str(payload.transferId)
    source_account_number = payload.sourceAccount.upper()
    destination_account_number = payload.destinationAccount.upper()
    amount = to_decimal_amount(payload.amount)

    ensure_transfer_not_exists(db, transfer_id)

    source = lock_and_get_account(db, source_account_number)
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Source account '{source_account_number}' not found"})

    if source.owner_id != current.id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Source account does not belong to caller"})

    if source.balance < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds in source account"})

    cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
    if not cfg or not cfg.bank_id:
        raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": "Branch is not registered with central bank"})

    source_bank_prefix = source_account_number[:3]
    destination_bank_prefix = destination_account_number[:3]
    is_same_bank = source_bank_prefix == destination_bank_prefix

    destination_bank_id = resolve_bank_id_from_prefix(db, destination_bank_prefix)
    if not is_same_bank and not destination_bank_id:
        raise HTTPException(status_code=404, detail={"code": "BANK_NOT_FOUND", "message": f"Destination bank with prefix '{destination_bank_prefix}' not found"})

    source.balance = (source.balance - amount).quantize(Decimal("0.01"))

    transfer = Transfer(
        transfer_id=transfer_id,
        source_account=source_account_number,
        destination_account=destination_account_number,
        amount=amount,
        status="completed",
        source_bank_id=cfg.bank_id,
        destination_bank_id=destination_bank_id if destination_bank_id else cfg.bank_id,
        direction="outgoing",
    )
    db.add(transfer)

    if is_same_bank:
        destination = lock_and_get_account(db, destination_account_number)
        if not destination:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{destination_account_number}' not found"})

        converted, rate, rate_time = convert_amount(amount, source.currency, destination.currency)
        destination.balance = (destination.balance + converted).quantize(Decimal("0.01"))

        if rate is not None:
            transfer.converted_amount = converted
            transfer.exchange_rate = rate
            transfer.rate_captured_at = rate_time

        db.commit()
        db.refresh(transfer)
        return TransferResponse(
            transferId=UUID(transfer.transfer_id),
            status=transfer.status,
            sourceAccount=transfer.source_account,
            destinationAccount=transfer.destination_account,
            amount=f"{transfer.amount:.2f}",
            convertedAmount=f"{transfer.converted_amount:.2f}" if transfer.converted_amount else None,
            exchangeRate=f"{transfer.exchange_rate:.6f}" if transfer.exchange_rate else None,
            rateCapturedAt=transfer.rate_captured_at,
            timestamp=transfer.updated_at,
        )

    target_currency = fetch_destination_currency(db, destination_bank_id, destination_account_number)
    if not target_currency:
        target_currency = source.currency
    converted, rate, rate_time = convert_amount(amount, source.currency, target_currency)

    if rate is not None:
        transfer.converted_amount = converted
        transfer.exchange_rate = rate
        transfer.rate_captured_at = rate_time

    ok, err = send_outgoing_interbank_transfer(
        db,
        cfg=cfg,
        transfer=transfer,
        destination_bank_id=destination_bank_id,
        converted_amount=converted,
    )

    if ok:
        transfer.status = "completed"
        db.commit()
        db.refresh(transfer)
        return TransferResponse(
            transferId=UUID(transfer.transfer_id),
            status=transfer.status,
            sourceAccount=transfer.source_account,
            destinationAccount=transfer.destination_account,
            amount=f"{transfer.amount:.2f}",
            convertedAmount=f"{transfer.converted_amount:.2f}" if transfer.converted_amount else None,
            exchangeRate=f"{transfer.exchange_rate:.6f}" if transfer.exchange_rate else None,
            rateCapturedAt=transfer.rate_captured_at,
            timestamp=transfer.updated_at,
        )

    schedule_pending(transfer)
    transfer.error_message = err
    db.commit()
    raise HTTPException(status_code=503, detail={"code": "DESTINATION_BANK_UNAVAILABLE", "message": "Destination bank is temporarily unavailable. Transfer has been queued for retry."})


@app.post("/api/v1/transfers/receive", response_model=InterBankTransferResponse, responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def receive_interbank_transfer(payload: InterBankTransferRequest, db: Session = Depends(get_db)):
    data = verify_and_decode_interbank(db, payload.jwt)
    transfer_id = data["transferId"]
    destination_account_number = data["destinationAccount"].upper()
    amount = to_decimal_amount(data["amount"])

    existing = db.query(Transfer).filter(Transfer.transfer_id == transfer_id).first()
    if existing:
        return InterBankTransferResponse(
            transferId=UUID(existing.transfer_id),
            status="completed" if existing.status == "completed" else "failed",
            destinationAccount=existing.destination_account,
            amount=f"{existing.amount:.2f}",
            timestamp=existing.updated_at,
        )

    destination = lock_and_get_account(db, destination_account_number)
    if not destination:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{destination_account_number}' not found"})

    destination.balance = (destination.balance + amount).quantize(Decimal("0.01"))
    transfer = Transfer(
        transfer_id=transfer_id,
        source_account=data["sourceAccount"].upper(),
        destination_account=destination_account_number,
        amount=amount,
        status="completed",
        direction="incoming",
        source_bank_id=data.get("sourceBankId"),
        destination_bank_id=data.get("destinationBankId"),
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)

    return InterBankTransferResponse(
        transferId=UUID(transfer.transfer_id),
        status="completed",
        destinationAccount=transfer.destination_account,
        amount=f"{transfer.amount:.2f}",
        timestamp=transfer.updated_at,
    )


@app.get("/api/v1/transfers/{transferId}", response_model=TransferResponse, responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 423: {"model": ErrorResponse}})
def get_transfer_status(transferId: UUID, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transfer = db.query(Transfer).filter(Transfer.transfer_id == str(transferId)).first()
    if not transfer:
        raise HTTPException(status_code=404, detail={"code": "TRANSFER_NOT_FOUND", "message": f"Transfer with ID '{transferId}' not found"})

    source = db.query(Account).filter(Account.account_number == transfer.source_account).first()
    if source and source.owner_id != current.id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot access this transfer"})

    if transfer.status == "pending" and check_timeout_and_refund(db, transfer):
        raise HTTPException(status_code=423, detail={"code": "TRANSFER_TIMEOUT", "message": "Transfer has timed out and cannot be modified or retried. Status is failed_timeout with refund processed."})

    return TransferResponse(
        transferId=UUID(transfer.transfer_id),
        status=transfer.status,
        sourceAccount=transfer.source_account,
        destinationAccount=transfer.destination_account,
        amount=f"{transfer.amount:.2f}",
        convertedAmount=f"{transfer.converted_amount:.2f}" if transfer.converted_amount else None,
        exchangeRate=f"{transfer.exchange_rate:.6f}" if transfer.exchange_rate else None,
        rateCapturedAt=transfer.rate_captured_at,
        pendingSince=transfer.pending_since,
        nextRetryAt=transfer.next_retry_at,
        retryCount=transfer.retry_count if transfer.status == "pending" else None,
        timestamp=transfer.updated_at,
        errorMessage=transfer.error_message,
    )


@app.get("/api/v1/users/{userId}/transfers", response_model=TransferHistoryResponse, responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})
def list_user_transfers(userId: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.id != userId:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot access another user transfer history"})

    owned_accounts = db.query(Account.account_number).filter(Account.owner_id == userId).all()
    numbers = [row[0] for row in owned_accounts]
    if not numbers:
        return TransferHistoryResponse(transfers=[])

    transfers = (
        db.query(Transfer)
        .filter((Transfer.source_account.in_(numbers)) | (Transfer.destination_account.in_(numbers)))
        .order_by(Transfer.created_at.desc())
        .all()
    )

    results = [
        TransferResponse(
            transferId=UUID(t.transfer_id),
            status=t.status,
            sourceAccount=t.source_account,
            destinationAccount=t.destination_account,
            amount=f"{t.amount:.2f}",
            convertedAmount=f"{t.converted_amount:.2f}" if t.converted_amount else None,
            exchangeRate=f"{t.exchange_rate:.6f}" if t.exchange_rate else None,
            rateCapturedAt=t.rate_captured_at,
            pendingSince=t.pending_since,
            nextRetryAt=t.next_retry_at,
            retryCount=t.retry_count if t.status == "pending" else None,
            timestamp=t.updated_at,
            errorMessage=t.error_message,
        )
        for t in transfers
    ]
    return TransferHistoryResponse(transfers=results)


@app.get("/health")
def healthcheck():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/admin/deposit", response_model=AccountCreationResponse, responses={404: {"model": ErrorResponse}})
def admin_deposit(accountNumber: str, amount: str, db: Session = Depends(get_db)):
    """
    Admin endpoint to deposit funds into an account (for testing only).
    Query params: accountNumber, amount (e.g., ?accountNumber=EST12345&amount=1000.00)
    """
    account = db.query(Account).filter(Account.account_number == accountNumber.upper()).with_for_update().first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account '{accountNumber}' not found"})

    try:
        deposit_amount = to_decimal_amount(amount)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "INVALID_AMOUNT", "message": "Amount must be decimal string with 2 places"})

    account.balance = (account.balance + deposit_amount).quantize(Decimal("0.01"))
    db.commit()
    db.refresh(account)

    return AccountCreationResponse(
        accountNumber=account.account_number,
        ownerId=account.owner_id,
        currency=account.currency,
        balance=f"{account.balance:.2f}",
        createdAt=account.created_at,
    )
