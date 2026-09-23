"""Generate the localhost cert.pem / key.pem pair without openssl.

Windows ships no openssl, and the pair is NOT optional (see CLAUDE.md: the
Vite proxy targets https://localhost:8340). Uses `cryptography`, which
install.ps1 installs. Existing files are left alone unless --force.
"""
from __future__ import annotations

import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cert_path, key_path = ROOT / "cert.pem", ROOT / "key.pem"
    if cert_path.exists() and key_path.exists() and "--force" not in sys.argv:
        print("cert.pem / key.pem already exist - leaving them alone.")
        return 0
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Wrote {cert_path.name} and {key_path.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
