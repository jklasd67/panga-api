from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt

from app.config import get_settings


settings = get_settings()


def ensure_keypair() -> tuple[str, str]:
    private_path = Path(settings.jwt_private_key_path)
    public_path = Path(settings.jwt_public_key_path)
    private_path.parent.mkdir(parents=True, exist_ok=True)

    if private_path.exists() and public_path.exists():
        return private_path.read_text(encoding="utf-8"), public_path.read_text(encoding="utf-8")

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    private_path.write_text(private_pem, encoding="utf-8")
    public_path.write_text(public_pem, encoding="utf-8")
    return private_pem, public_pem


def pem_to_base64_der_public_key(pem_text: str) -> str:
    public_key = serialization.load_pem_public_key(pem_text.encode("utf-8"))
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("utf-8")


def base64_der_to_pem_public_key(base64_text: str) -> str:
    der = base64.b64decode(base64_text)
    public_key = serialization.load_der_public_key(der)
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def build_interbank_jwt(payload: dict, private_key_pem: str) -> str:
    return jwt.encode(payload, private_key_pem, algorithm="ES256")


def decode_interbank_jwt(token: str, public_key_pem: str) -> dict:
    return jwt.decode(token, public_key_pem, algorithms=["ES256"])


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def in_seconds(seconds: int) -> datetime:
    return now_utc() + timedelta(seconds=seconds)
