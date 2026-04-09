from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import pem_to_base64_der_public_key, base64_der_to_pem_public_key
from app.models import BankDirectoryEntry, BranchConfig


settings = get_settings()


def _api_url(path: str) -> str:
    base = settings.central_bank_base_url.rstrip("/") + "/"
    if "api/v1" not in base:
        base = urljoin(base, "api/v1/")
    return urljoin(base, path.lstrip("/"))


def register_branch_if_needed(db: Session, public_key_pem: str, private_key_pem: str) -> BranchConfig:
    cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
    if cfg and cfg.bank_id:
        return cfg

    if not cfg:
        cfg = BranchConfig(
            id=1,
            bank_name=settings.branch_bank_name,
            address=settings.branch_base_url,
            public_key=public_key_pem,
            private_key=private_key_pem,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

    public_address = cfg.address
    if not public_address.startswith("http://") and not public_address.startswith("https://"):
        public_address = f"https://{public_address}"

    payload = {
        "name": cfg.bank_name,
        "address": public_address,
        "publicKey": pem_to_base64_der_public_key(public_key_pem),
    }
    with httpx.Client(timeout=15.0) as client:
        response = client.post(_api_url("/banks"), json=payload)

    if response.status_code not in (200, 201, 409):
        raise RuntimeError(f"Central bank registration failed: {response.status_code} {response.text}")

    if response.status_code == 409 and not cfg.bank_id:
        sync_directory(db)
        found = db.query(BankDirectoryEntry).filter(BankDirectoryEntry.address == cfg.address).first()
        if found:
            cfg.bank_id = found.bank_id
            cfg.expires_at = datetime.now(timezone.utc)
            db.commit()
        return cfg

    data = response.json()
    cfg.bank_id = data["bankId"]
    cfg.expires_at = _parse_dt(data["expiresAt"])
    db.commit()
    db.refresh(cfg)
    return cfg


def send_heartbeat(db: Session) -> None:
    cfg = db.query(BranchConfig).filter(BranchConfig.id == 1).first()
    if not cfg or not cfg.bank_id:
        return

    payload = {"timestamp": datetime.now(timezone.utc).isoformat()}
    with httpx.Client(timeout=15.0) as client:
        response = client.post(_api_url(f"/banks/{cfg.bank_id}/heartbeat"), json=payload)

    if response.status_code == 200:
        data = response.json()
        cfg.last_heartbeat_at = _parse_dt(data["receivedAt"])
        cfg.expires_at = _parse_dt(data["expiresAt"])
        db.commit()


def sync_directory(db: Session) -> datetime:
    with httpx.Client(timeout=20.0) as client:
        response = client.get(_api_url("/banks"))

    if response.status_code != 200:
        raise RuntimeError("Central bank directory unavailable")

    data = response.json()
    synced_at = _parse_dt(data["lastSyncedAt"])

    db.query(BankDirectoryEntry).delete()
    for bank in data["banks"]:
        entry = BankDirectoryEntry(
            bank_id=bank["bankId"],
            name=bank["name"],
            address=bank["address"],
            public_key=base64_der_to_pem_public_key(bank["publicKey"]),
            last_heartbeat=_parse_dt(bank["lastHeartbeat"]),
            status=bank["status"],
            last_synced_at=synced_at,
        )
        db.add(entry)
    db.commit()
    return synced_at


def get_bank_from_cache_or_central(db: Session, bank_id: str) -> BankDirectoryEntry | None:
    bank = db.query(BankDirectoryEntry).filter(BankDirectoryEntry.bank_id == bank_id).first()
    if bank:
        return bank

    with httpx.Client(timeout=15.0) as client:
        response = client.get(_api_url(f"/banks/{bank_id}"))

    if response.status_code != 200:
        return None

    data = response.json()
    entry = BankDirectoryEntry(
        bank_id=data["bankId"],
        name=data["name"],
        address=data["address"],
        public_key=base64_der_to_pem_public_key(data["publicKey"]),
        last_heartbeat=_parse_dt(data["lastHeartbeat"]),
        status=data["status"],
        last_synced_at=datetime.now(timezone.utc),
    )
    db.merge(entry)
    db.commit()
    return entry


def get_exchange_rates() -> dict:
    with httpx.Client(timeout=15.0) as client:
        response = client.get(_api_url("/exchange-rates"))
    if response.status_code != 200:
        raise RuntimeError("Exchange rates unavailable")
    return response.json()


def _parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
