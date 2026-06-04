from maintenance import clear_software_cache, clear_software_data


def test_clear_software_cache_deletes_only_regenerable_paths(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (tmp_path / "ui_pages" / "__pycache__").mkdir(parents=True)
    (tmp_path / "ui_pages" / "__pycache__" / "page.pyc").write_bytes(b"cache")
    (tmp_path / ".venv" / "Lib" / "site-packages" / "__pycache__").mkdir(parents=True)
    (tmp_path / ".venv" / "Lib" / "site-packages" / "__pycache__" / "dependency.pyc").write_bytes(
        b"cache"
    )
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / "debug_payload.log").write_text("debug", encoding="utf-8")
    (tmp_path / "data" / "voice" / "anchor" / "generated").mkdir(parents=True)
    (tmp_path / "data" / "voice" / "anchor" / "generated" / "anchor.wav").write_bytes(b"wav")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    result = clear_software_cache(tmp_path)

    assert result.deleted_count >= 4
    assert not (tmp_path / "__pycache__").exists()
    assert not (tmp_path / "ui_pages" / "__pycache__").exists()
    assert (tmp_path / ".venv" / "Lib" / "site-packages" / "__pycache__").exists()
    assert not (tmp_path / ".pytest_cache").exists()
    assert not (tmp_path / "debug_payload.log").exists()
    assert not (tmp_path / "data" / "voice" / "anchor" / "generated").exists()
    assert (tmp_path / "settings.json").exists()
    assert (tmp_path / "auth.json").exists()


def test_clear_software_data_resets_user_data_but_not_source_like_files(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "voice").mkdir(parents=True)
    (tmp_path / "data" / "voice" / "sample.wav").write_bytes(b"wav")
    (tmp_path / "browser_data").mkdir()
    (tmp_path / "browser_data" / "state").write_text("x", encoding="utf-8")
    (tmp_path / "ui.py").write_text("source", encoding="utf-8")
    (tmp_path / ".env").write_text("secret", encoding="utf-8")

    result = clear_software_data(tmp_path)

    assert result.deleted_count >= 4
    assert not (tmp_path / "settings.json").exists()
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "browser_data").exists()
    assert (tmp_path / "ui.py").exists()
    assert (tmp_path / ".env").exists()
