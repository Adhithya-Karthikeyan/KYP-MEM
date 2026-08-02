import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolate_vault(tmp_path, monkeypatch):
    """Never let a test touch the user's real vault or config.

    The config file is redirected too: load_config reads the CONFIG_FILE
    module global at call time, so without this a developer's own
    ``embedding_model`` setting would leak into every test and change which
    embedder (and which similarity floors) the suite runs under.
    """
    monkeypatch.setenv("KYP_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("KYP_NO_VECTOR", raising=False)
    from kyp_mem import config, vector

    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    vector.reset_vector_db()
    yield
    vector.reset_vector_db()


@pytest.fixture
def no_vector(monkeypatch):
    """Keyword-only mode — fast, no embedding runtime."""
    monkeypatch.setenv("KYP_NO_VECTOR", "1")


def write(vault_root: Path, rel: str, body: str):
    p = vault_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p
