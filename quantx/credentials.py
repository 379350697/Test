"""Credential loading helpers for exchange clients.

Security goals:
- Only read secrets from environment variables.
- Provide safe diagnostics without exposing secret values.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


class CredentialLoadError(ValueError):
    """Raised when required credentials are missing from environment variables."""


@dataclass(frozen=True, slots=True)
class BinanceCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True, slots=True)
class OKXCredentials:
    api_key: str
    api_secret: str
    passphrase: str


def _require_non_empty_env(name: str, env: Mapping[str, str]) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise CredentialLoadError(f"missing_required_env:{name}")
    return value


def load_binance_credentials(env: Mapping[str, str] | None = None) -> BinanceCredentials:
    source = env or os.environ
    return BinanceCredentials(
        api_key=_require_non_empty_env("BINANCE_API_KEY", source),
        api_secret=_require_non_empty_env("BINANCE_API_SECRET", source),
    )


def load_okx_credentials(env: Mapping[str, str] | None = None) -> OKXCredentials:
    source = env or os.environ
    return OKXCredentials(
        api_key=_require_non_empty_env("OKX_API_KEY", source),
        api_secret=_require_non_empty_env("OKX_API_SECRET", source),
        passphrase=_require_non_empty_env("OKX_PASSPHRASE", source),
    )


def credential_presence_snapshot(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    source = env or os.environ
    required = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_PASSPHRASE",
    ]
    return {name: bool(source.get(name, "").strip()) for name in required}
