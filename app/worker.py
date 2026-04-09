from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time

from app.central_bank import send_heartbeat, sync_directory
from app.config import get_settings
from app.db import SessionLocal, Base, engine
from app.models import Transfer, Account, BranchConfig
from app.service import check_timeout_and_refund, send_outgoing_interbank_transfer
from app.service import resolve_bank_id_from_prefix


settings = get_settings()


def process_pending_transfers() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        pending = (
            db.query(Transfer)
            .filter(Transfer.status == "pending", Transfer.next_retry_at <= now)
            .all()
        )

        cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
        if not cfg or not cfg.bank_id:
            return

        for transfer in pending:
            if check_timeout_and_refund(db, transfer):
                continue

            converted = transfer.converted_amount if transfer.converted_amount else transfer.amount
            destination_bank_id = transfer.destination_bank_id or resolve_bank_id_from_prefix(db, transfer.destination_account[:3])
            if not destination_bank_id:
                transfer.retry_count += 1
                transfer.error_message = "Destination bank not found in cache"
                transfer.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                db.commit()
                continue

            ok, err = send_outgoing_interbank_transfer(
                db=db,
                cfg=cfg,
                transfer=transfer,
                destination_bank_id=destination_bank_id,
                converted_amount=converted,
            )

            if ok:
                transfer.status = "completed"
                transfer.error_message = None
                transfer.next_retry_at = None
                db.commit()
                continue

            transfer.retry_count += 1
            backoff = min(2 ** transfer.retry_count * 60, settings.pending_retry_max_seconds)
            transfer.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            transfer.error_message = err
            db.commit()
    finally:
        db.close()


def run() -> None:
    Base.metadata.create_all(bind=engine)
    heartbeat_counter = 0
    sync_counter = 0

    while True:
        db = SessionLocal()
        try:
            if heartbeat_counter <= 0:
                send_heartbeat(db)
                heartbeat_counter = settings.heartbeat_interval_seconds

            if sync_counter <= 0:
                try:
                    sync_directory(db)
                except Exception:
                    pass
                sync_counter = settings.directory_cache_ttl_seconds
        finally:
            db.close()

        process_pending_transfers()
        time.sleep(10)
        heartbeat_counter -= 10
        sync_counter -= 10


if __name__ == "__main__":
    run()
