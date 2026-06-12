import logging
import os
import sys
from pathlib import Path



sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))
from dry_run import get_dry_run_mode

def test_invalid_dry_run_env_defaults_to_true(monkeypatch, caplog):
    monkeypatch.setenv("DRY_RUN_MODE", "invalid")
    
    with caplog.at_level(logging.WARNING):
        dry_run = get_dry_run_mode()
        
    assert dry_run is True
    assert "Invalid DRY_RUN_MODE env var: invalid" in caplog.text

def test_missing_env_uses_default(monkeypatch):
    monkeypatch.delenv("DRY_RUN_MODE", raising=False)
    assert get_dry_run_mode() is True
    assert get_dry_run_mode(default=False) is False

def test_empty_env_uses_default(monkeypatch):
    monkeypatch.setenv("DRY_RUN_MODE", "   ")
    assert get_dry_run_mode() is True

def test_true_values(monkeypatch):
    for val in ["true", "1", "yes", "TRUE", " Yes "]:
        monkeypatch.setenv("DRY_RUN_MODE", val)
        assert get_dry_run_mode() is True

def test_false_values(monkeypatch):
    for val in ["false", "0", "no", "FALSE", " No "]:
        monkeypatch.setenv("DRY_RUN_MODE", val)
        assert get_dry_run_mode() is False
