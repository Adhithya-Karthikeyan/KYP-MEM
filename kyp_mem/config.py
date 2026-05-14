"""KYP-MEM configuration — stored at ~/.kyp-mem/config.json"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".kyp-mem"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATS_FILE = CONFIG_DIR / "token_stats.json"
DEFAULT_VAULT = str(CONFIG_DIR / "vault")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            return {"vault_path": DEFAULT_VAULT}
    return {"vault_path": DEFAULT_VAULT}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_vault_path() -> str:
    env = os.environ.get("KYP_VAULT")
    if env:
        return env
    config = load_config()
    return config.get("vault_path", DEFAULT_VAULT)


def get_session_model() -> str:
    config = load_config()
    return config.get("session_model", "claude-haiku-4-5-20251001")
