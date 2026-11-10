"""Vercel mounts the deployment bundle read-only; only the temp dir is writable.

Nothing local catches this - `tmp_path` is always writable and PROJECT_ROOT is too -
so these tests assert the *destination* of each runtime write rather than trying to
simulate a read-only filesystem.
"""

import tempfile
from pathlib import Path

import pytest

from gtm_engine.config.loader import PROJECT_ROOT, is_serverless, runtime_dir


@pytest.fixture
def on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")


def test_not_serverless_by_default(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert not is_serverless()
    assert runtime_dir() == PROJECT_ROOT / "data"


def test_serverless_detected_on_vercel_and_lambda(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert is_serverless()
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "gtm-api")
    assert is_serverless()


def test_runtime_dir_is_writable_and_outside_the_bundle(on_vercel):
    d = runtime_dir()
    assert d.is_relative_to(Path(tempfile.gettempdir()))
    assert not d.is_relative_to(PROJECT_ROOT), "writes would hit the read-only bundle"
    probe = d / "probe.txt"
    probe.write_text("ok", encoding="utf-8")   # would raise OSError on Vercel if wrong
    assert probe.read_text(encoding="utf-8") == "ok"
    probe.unlink()


def test_api_ledger_never_writes_into_the_repo(on_vercel):
    from gtm_engine.api.main import _read_only_ledger

    ledger = _read_only_ledger("test-retail")
    assert not ledger.path.is_relative_to(PROJECT_ROOT)
    # It must still be usable: save() is what would have raised OSError in-request.
    ledger.record_stop("someone@example.com", "unsubscribed")
    assert ledger.path.exists()
    ledger.path.unlink()


def test_ledger_still_written_in_place_off_serverless(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    from gtm_engine.api.main import _read_only_ledger

    # Locally and in GitHub Actions the checkout is writable and the ledger is committed
    # back, so it must keep landing in leads/ rather than being diverted to scratch.
    assert _read_only_ledger("test-retail").path.is_relative_to(PROJECT_ROOT / "leads")
