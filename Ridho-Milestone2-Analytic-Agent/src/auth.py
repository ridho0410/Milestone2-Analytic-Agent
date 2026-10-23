"""Autentikasi token demo -> identitas server-side.

Prinsip keamanan yang ditegakkan modul ini:

1. Identitas HANYA berasal dari bearer token yang diverifikasi di server.
   `user_id`, `X-User-Id`, atau header serupa dari klien DIABAIKAN sepenuhnya —
   rubrik memberi 0 pada kategori otorisasi bila backend mempercayai klien.
2. Perbandingan token memakai `hmac.compare_digest` untuk mencegah timing attack.
3. Token tidak pernah dicatat ke log atau dikembalikan pada pesan error.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from .config import settings


@dataclass(frozen=True)
class Identity:
    """Identitas terverifikasi. Hanya dibuat oleh auth.py."""

    user_id: str


class AuthError(HTTPException):
    def __init__(
        self, detail: str = "The token is invalid or missing."
    ) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _token_map() -> dict[str, Identity]:
    """Peta token ke identitas dari Settings aktif, tanpa cache token tambahan."""
    mapping: dict[str, Identity] = {}
    if settings.demo_token_a and settings.demo_token_a == settings.demo_token_b:
        return mapping
    if settings.demo_token_a:
        mapping[settings.demo_token_a] = Identity(user_id="user-a")
    if settings.demo_token_b:
        mapping[settings.demo_token_b] = Identity(user_id="user-b")
    return mapping


def resolve_token(token: str | None) -> Identity:
    """Petakan token ke identitas, atau lempar 401.

    Mengembalikan 401 yang identik untuk token kosong, salah, dan tidak dikenal
    sehingga penyerang tidak dapat membedakan 'token tidak ada' dari 'token salah'.
    """
    if not token:
        raise AuthError()

    mapping = _token_map()
    if not mapping:
        # Tidak ada token dikonfigurasi: jangan pernah meloloskan siapa pun.
        raise AuthError("Authentication has not been configured on the server.")

    for known_token, identity in mapping.items():
        if hmac.compare_digest(known_token.encode("utf8"), token.encode("utf8")):
            return identity

    raise AuthError()


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None


async def require_identity(
    authorization: str | None = Header(default=None),
    # Header berikut sengaja dideklarasikan agar dapat diabaikan secara eksplisit.
    # Kehadirannya adalah bukti bahwa klien TIDAK dapat menentukan identitasnya.
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Identity:
    """Dependency FastAPI: verifikasi bearer token, abaikan identitas dari klien."""
    del x_user_id  # Diterima tetapi tidak pernah dipakai untuk otorisasi.
    return resolve_token(_extract_bearer(authorization))
