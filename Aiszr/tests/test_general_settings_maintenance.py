import ast
from pathlib import Path


def _source():
    return (Path(__file__).resolve().parents[1] / "ui_pages" / "general_settings.py").read_text(
        encoding="utf-8"
    )


def test_settings_page_has_clear_cache_and_data_buttons():
    source = _source()

    assert "清除缓存" in source
    assert "清除软件数据" in source
    assert "clear_software_cache" in source
    assert "clear_software_data" in source


def test_clear_data_handler_requires_confirmation():
    tree = ast.parse(_source())
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_clear_data"
    )
    handler_source = ast.unparse(handler)

    assert "QMessageBox.question" in handler_source
    assert "不可恢复" in handler_source


def test_clear_handlers_show_warning_on_failure():
    tree = ast.parse(_source())
    handlers = {
        node.name: ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"_on_clear_cache", "_on_clear_data"}
    }

    assert "except Exception" in handlers["_on_clear_cache"]
    assert "QMessageBox.warning" in handlers["_on_clear_cache"]
    assert "except Exception" in handlers["_on_clear_data"]
    assert "QMessageBox.warning" in handlers["_on_clear_data"]
