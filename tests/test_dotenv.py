from gtm_engine.config.loader import load_dotenv


def test_dotenv_comments_do_not_become_credentials(tmp_path, monkeypatch):
    key = "TEST_DOTENV_CREDENTIAL"
    monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"{key}=sandbox-client-id  # sandbox app\n", encoding="utf-8")
    assert load_dotenv(env) == 1
    import os
    assert os.environ[key] == "sandbox-client-id"


def test_dotenv_preserves_hashes_in_passwords(tmp_path, monkeypatch):
    import os
    keys = ["TEST_DOTENV_UNQUOTED", "TEST_DOTENV_SINGLE", "TEST_DOTENV_DOUBLE"]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        f"{keys[0]}=password#123\n"
        f"{keys[1]}='password #123' # explanation\n"
        f'{keys[2]}="password #123" # explanation\n', encoding="utf-8"
    )
    assert load_dotenv(env) == 3
    assert os.environ[keys[0]] == "password#123"
    assert os.environ[keys[1]] == os.environ[keys[2]] == "password #123"


def test_dotenv_preserves_existing_environment(tmp_path, monkeypatch):
    import os
    key = "TEST_DOTENV_EXISTING"
    monkeypatch.setenv(key, "runtime-value")
    env = tmp_path / ".env"
    env.write_text(f"{key}=file-value\n", encoding="utf-8")
    assert load_dotenv(env) == 0
    assert os.environ[key] == "runtime-value"
