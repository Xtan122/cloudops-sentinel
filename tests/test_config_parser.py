import json
import sys
import copy
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda"))
from shared.config_parser import load_config, MINIMAL_SAFE_CONFIG

def test_config_file_false_and_env_missing_keeps_false(monkeypatch, tmp_path):
    monkeypatch.delenv("DRY_RUN_MODE", raising=False)
    
    config_data = copy.deepcopy(MINIMAL_SAFE_CONFIG)
    config_data["dry_run_mode"] = False
    
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))
    
    config = load_config(str(config_file))
    assert config["dry_run_mode"] is False

def test_config_file_false_but_env_invalid_forces_true(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN_MODE", "invalid")
    
    config_data = copy.deepcopy(MINIMAL_SAFE_CONFIG)
    config_data["dry_run_mode"] = False
    
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))
    
    config = load_config(str(config_file))
    assert config["dry_run_mode"] is True

def test_config_file_false_but_env_true_overrides_to_true(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN_MODE", "true")
    
    config_data = copy.deepcopy(MINIMAL_SAFE_CONFIG)
    config_data["dry_run_mode"] = False
    
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))
    
    config = load_config(str(config_file))
    assert config["dry_run_mode"] is True

def test_config_file_true_but_env_false_overrides_to_false(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN_MODE", "false")
    
    config_data = copy.deepcopy(MINIMAL_SAFE_CONFIG)
    config_data["dry_run_mode"] = True
    
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))
    
    config = load_config(str(config_file))
    assert config["dry_run_mode"] is False

def test_invalid_json_returns_minimal_safe_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN_MODE", raising=False)
    config_file = tmp_path / "config.json"
    config_file.write_text("{ invalid json")

    config = load_config(str(config_file))

    assert config == MINIMAL_SAFE_CONFIG
    assert config["dry_run_mode"] is True

def test_invalid_schema_returns_minimal_safe_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN_MODE", raising=False)

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "version": "1.0",
        "dry_run_mode": "false",
        "guardrails": {}
    }))

    config = load_config(str(config_file))

    assert config == MINIMAL_SAFE_CONFIG
    assert config["dry_run_mode"] is True