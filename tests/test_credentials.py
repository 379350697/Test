from __future__ import annotations

import pytest

from quantx.credentials import (
    CredentialLoadError,
    credential_presence_snapshot,
    load_binance_credentials,
    load_okx_credentials,
)


def test_load_binance_credentials_success():
    creds = load_binance_credentials({"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"})
    assert creds.api_key == "k"
    assert creds.api_secret == "s"


def test_load_okx_credentials_success():
    creds = load_okx_credentials({"OKX_API_KEY": "k", "OKX_API_SECRET": "s", "OKX_PASSPHRASE": "p"})
    assert creds.api_key == "k"
    assert creds.api_secret == "s"
    assert creds.passphrase == "p"


def test_load_credentials_missing_env_raises():
    with pytest.raises(CredentialLoadError, match="BINANCE_API_SECRET"):
        load_binance_credentials({"BINANCE_API_KEY": "k"})

    with pytest.raises(CredentialLoadError, match="OKX_PASSPHRASE"):
        load_okx_credentials({"OKX_API_KEY": "k", "OKX_API_SECRET": "s"})


def test_presence_snapshot():
    snap = credential_presence_snapshot(
        {
            "BINANCE_API_KEY": "k",
            "BINANCE_API_SECRET": "",
            "OKX_API_KEY": "k",
            "OKX_API_SECRET": "s",
        }
    )
    assert snap["BINANCE_API_KEY"] is True
    assert snap["BINANCE_API_SECRET"] is False
    assert snap["OKX_API_KEY"] is True
    assert snap["OKX_API_SECRET"] is True
    assert snap["OKX_PASSPHRASE"] is False
