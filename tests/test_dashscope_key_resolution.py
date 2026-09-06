from pathlib import Path

from app.config import resolve_dashscope_api_key


def test_dotenv_key_has_priority_over_stale_environment(monkeypatch, tmp_path: Path):
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=new-dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-environment-key")
    assert resolve_dashscope_api_key(tmp_path) == "new-dotenv-key"


def test_local_key_file_has_priority_over_stale_environment(monkeypatch, tmp_path: Path):
    key_file = tmp_path / "config" / ".dashscope_api_key"
    key_file.parent.mkdir()
    key_file.write_text("new-local-key\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-environment-key")
    assert resolve_dashscope_api_key(tmp_path) == "new-local-key"


def test_environment_is_used_when_no_local_secret_source_exists(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-key")
    assert resolve_dashscope_api_key(tmp_path) == "environment-key"
