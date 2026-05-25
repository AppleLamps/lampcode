from __future__ import annotations

import datetime
import ipaddress
import ssl
from pathlib import Path
from typing import Any


def expand_path(path: str) -> Path:
    return Path(path).expanduser()


def load_ssl_context(cert_file: Path, key_file: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def cert_fingerprint(cert_file: Path) -> str | None:
    try:
        import hashlib

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        data = cert_file.read_bytes()
        cert = x509.load_pem_x509_certificate(data, default_backend())
        digest = hashlib.sha256(cert.public_bytes()).hexdigest()
        return digest[:16]
    except Exception:
        return None


def cert_expiry(cert_file: Path) -> datetime.datetime | None:
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert = x509.load_pem_x509_certificate(cert_file.read_bytes(), default_backend())
        return cert.not_valid_after_utc.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def generate_self_signed_cert(
    cert_file: Path,
    key_file: Path,
    *,
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError(
            "Self-signed cert generation requires cryptography (pip install -e '.[marketplace]')"
        ) from exc

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"agent-cli-{host}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "agent-cli"),
        ]
    )
    alt_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    try:
        ipaddress.ip_address(host)
        alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        alt_names.append(x509.DNSName(host))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    fp = cert_fingerprint(cert_file)
    return {"cert_file": str(cert_file), "key_file": str(key_file), "fingerprint_sha256_prefix": fp}
