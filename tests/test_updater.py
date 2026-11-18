import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from spoke import updater

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_code_zip", ROOT / "scripts" / "build_code_zip.py")
build_code_zip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_code_zip)

CODE_URL, SUM_URL, API_URL = "asset/code", "asset/sum", f"{updater.API}/repos/{updater.REPO}/releases/latest"


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def good(version="1.1.0", shell=1):
    return archive({"spoke/__init__.py": f'__version__ = "{version}"\nSHELL_API = {shell}\n', "spoke/app.py": "x = 1\n"})


def release(version="1.1.0"):
    return updater.Release(version, "заметки", "https://example/release", CODE_URL, SUM_URL)


def serve(monkeypatch, data, checksum=None):
    sums = checksum or hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(updater, "fetch", lambda url, token="", accept="": {CODE_URL: data, SUM_URL: f"{sums}  spoke-code.zip\n".encode()}[url])


def test_parse_version_compares_numerically():
    assert updater.parse_version("v1.10.0") > updater.parse_version("1.9.9")
    assert updater.parse_version("1.0.0") == updater.parse_version("v1.0.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("nightly")


def test_check_returns_release_only_when_newer(monkeypatch):
    def api(version):
        body = {"tag_name": f"v{version}", "body": "notes", "html_url": "page", "assets": [
            {"name": f"spoke-code-{version}.zip", "url": CODE_URL}, {"name": f"spoke-code-{version}.zip.sha256", "url": SUM_URL}]}
        monkeypatch.setattr(updater, "fetch", lambda url, token="", accept="": json.dumps(body).encode())
    api("99.0.0")
    found = updater.check()
    assert found.version == "99.0.0" and found.code_url == CODE_URL and found.checksum_url == SUM_URL
    api(updater.__version__)
    assert updater.check() is None


def test_release_without_code_archive_is_reported(monkeypatch):
    monkeypatch.setattr(updater, "fetch", lambda *a, **k: json.dumps({"tag_name": "v2.0.0", "assets": []}).encode())
    with pytest.raises(updater.UpdateError, match="нет архива"):
        updater.latest_release()


def test_apply_installs_new_code_and_replaces_old_one(tmp_path, monkeypatch):
    code = tmp_path / "code"
    serve(monkeypatch, good("1.1.0"))
    updater.apply(release("1.1.0"), code_dir=code, shell_api=1)
    assert "1.1.0" in (code / "spoke" / "__init__.py").read_text()
    serve(monkeypatch, good("1.2.0"))
    updater.apply(release("1.2.0"), code_dir=code, shell_api=1)
    assert "1.2.0" in (code / "spoke" / "__init__.py").read_text()
    assert not (tmp_path / "code.new").exists() and not (tmp_path / "code.old").exists()


@pytest.mark.parametrize("data,checksum,message", [
    (good(), "0" * 64, "Контрольная сумма"),
    (archive({"spoke/__init__.py": "def broken(:\n"}), None, "с ошибкой"),
    (archive({"spoke/__init__.py": 'x = 1\n'}), None, "Версия в архиве"),
    (archive({"other/evil.py": "x = 1\n"}), None, "лишние файлы"),
    (archive({"spoke/../evil.py": "x = 1\n"}), None, "лишние файлы"),
    (archive({"app.py": "x = 1\n"}), None, "лишние файлы"),
])
def test_bad_archives_are_rejected_and_old_code_stays(tmp_path, monkeypatch, data, checksum, message):
    code = tmp_path / "code"
    serve(monkeypatch, good("1.1.0"))
    updater.apply(release("1.1.0"), code_dir=code, shell_api=1)
    serve(monkeypatch, data, checksum)
    with pytest.raises(updater.UpdateError, match=message):
        updater.apply(release("1.1.0"), code_dir=code, shell_api=1)
    assert "1.1.0" in (code / "spoke" / "__init__.py").read_text() and not (tmp_path / "code.new").exists()


def test_update_needing_newer_shell_is_refused_with_download_hint(tmp_path, monkeypatch):
    serve(monkeypatch, good("2.0.0", shell=2))
    with pytest.raises(updater.UpdateError, match="нужна новая сборка"):
        updater.apply(release("2.0.0"), code_dir=tmp_path / "code", shell_api=1)
    assert not (tmp_path / "code").exists()


def test_build_code_zip_produces_archive_that_apply_accepts(tmp_path, monkeypatch):
    built = build_code_zip.build(tmp_path / "out")
    version = build_code_zip.version()
    assert built.name == f"spoke-code-{version}.zip"
    digest = (tmp_path / "out" / f"{built.name}.sha256").read_text().split()[0]
    assert digest == hashlib.sha256(built.read_bytes()).hexdigest()
    names = zipfile.ZipFile(built).namelist()
    assert "spoke/__init__.py" in names and not any("__pycache__" in name for name in names)
    serve(monkeypatch, built.read_bytes())
    updater.apply(release(version), code_dir=tmp_path / "code", shell_api=1)
    assert (tmp_path / "code" / "spoke" / "app.py").exists()


def test_release_notes_hide_markdown_and_github_boilerplate():
    generated = """## What's Changed
* Лицензия MIT by @YamixCode in https://github.com/YamixCode/spoke/pull/4
* Окно настроек в системном стиле by @YamixCode in https://github.com/YamixCode/spoke/pull/5
* Версия 1.0.1 by @YamixCode in https://github.com/YamixCode/spoke/pull/6


**Full Changelog**: https://github.com/YamixCode/spoke/compare/v1.0.0...v1.0.1
"""
    assert updater.release_notes(generated) == "• Лицензия MIT\n• Окно настроек в системном стиле"
    written = "## Что нового\n\n- Быстрее **запуск**, см. [описание](https://example.com/x)\n- Исправлена `вставка`\n"
    assert updater.release_notes(written) == "Что нового\n\n• Быстрее запуск, см. описание\n• Исправлена вставка"
    assert updater.release_notes("") == ""
