"""Обновление Spoke через GitHub Releases: код ставится в Application Support, сам .app и разрешения macOS не меняются."""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import certifi

from spoke import SHELL_API, __version__

REPO = "YamixCode/spoke"
API = "https://api.github.com"
CODE_DIR = Path.home() / "Library" / "Application Support" / "Spoke" / "code"
TIMEOUT = 15
REDIRECTS = (301, 302, 303, 307, 308)


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str
    page: str
    code_url: str
    checksum_url: str


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text.lstrip("vV"))
    if not numbers:
        raise UpdateError(f"Непонятная версия: {text}")
    return tuple(int(number) for number in numbers[:3])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_error(error: urllib.error.HTTPError, authorized: bool) -> UpdateError:
    if error.code in (401, 403) and authorized:
        return UpdateError("GitHub отклонил токен или исчерпан лимит запросов")
    if error.code in (404, 401, 403):
        return UpdateError("Релизы не найдены: репозиторий закрыт, нужен токен GitHub в настройках")
    return UpdateError(f"GitHub ответил ошибкой {error.code}")


def _opener(*handlers) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(*handlers, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))


def fetch(url: str, token: str = "", accept: str = "application/vnd.github+json") -> bytes:
    headers = {"Accept": accept, "User-Agent": "Spoke", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    opener = _opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in REDIRECTS and error.headers.get("Location"):
            plain = urllib.request.Request(error.headers["Location"], headers={"User-Agent": "Spoke"})
            try:
                with _opener().open(plain, timeout=TIMEOUT) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as inner:
                raise UpdateError("Не удалось скачать файл обновления") from inner
        raise _http_error(error, bool(token)) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise UpdateError(f"Нет соединения с GitHub ({getattr(error, 'reason', error)})") from error


def latest_release(token: str = "") -> Release:
    data = json.loads(fetch(f"{API}/repos/{REPO}/releases/latest", token))
    assets = {asset["name"]: asset["url"] for asset in data.get("assets", [])}
    code = next((name for name in assets if re.fullmatch(r"spoke-code-[\d.]+\.zip", name)), None)
    if code is None or f"{code}.sha256" not in assets:
        raise UpdateError("В последнем релизе нет архива с кодом обновления")
    return Release(str(data["tag_name"]).lstrip("vV"), data.get("body") or "", data.get("html_url", ""), assets[code], assets[f"{code}.sha256"])


def check(token: str = "") -> Release | None:
    release = latest_release(token)
    return release if parse_version(release.version) > parse_version(__version__) else None


def release_notes(markdown: str) -> str:
    items, plain = [], []
    for raw in markdown.splitlines():
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw).strip()
        if not line or re.match(r"\W*full changelog", line, re.IGNORECASE):
            continue
        bullet = re.match(r"[*-]\s+(.*?)(?:\s+by\s+@\S+.*)?$", line)
        if bullet:
            if not re.match(r"(версия|bump|release)\b", bullet.group(1), re.IGNORECASE):
                items.append("• " + re.sub(r"\*\*|__|`", "", bullet.group(1)).strip())
            continue
        if not re.match(r"#+\s*what'?s changed", line, re.IGNORECASE):
            plain.append(re.sub(r"https?://\S+", "", re.sub(r"^#+\s*|\*\*|__|`", "", line)).strip())
    head = [line for line in plain if line]
    return "\n".join(head + ([""] if head and items else []) + items).strip()


def installed_shell_api() -> int | None:
    resources = os.environ.get("RESOURCEPATH")
    if not resources:
        return None
    try:
        return int(plistlib.loads((Path(resources).parent / "Info.plist").read_bytes())["CFBundleVersion"])
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return None


def _extract(archive: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for name in bundle.namelist():
            if not name.startswith("spoke/") or ".." in Path(name).parts:
                raise UpdateError("Архив обновления содержит лишние файлы")
        bundle.extractall(destination)


def _validate(package: Path, release: Release, shell_api: int | None) -> None:
    init = package / "__init__.py"
    if not init.exists():
        raise UpdateError("В архиве нет пакета spoke")
    for path in package.rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as error:
            raise UpdateError(f"Обновление с ошибкой в {path.name}") from error
    text = init.read_text(encoding="utf-8")
    declared = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not declared or parse_version(declared.group(1)) != parse_version(release.version):
        raise UpdateError("Версия в архиве не совпадает с релизом")
    required = re.search(r"SHELL_API\s*=\s*(\d+)", text)
    if shell_api is not None and required and int(required.group(1)) > shell_api:
        raise UpdateError(f"Версии {release.version} нужна новая сборка приложения: скачайте DMG на {release.page}")


def apply(release: Release, token: str = "", code_dir: Path = CODE_DIR, shell_api: int | None = None) -> None:
    archive = fetch(release.code_url, token, "application/octet-stream")
    expected = fetch(release.checksum_url, token, "application/octet-stream").decode().split()[0].lower()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise UpdateError("Контрольная сумма архива не совпала, обновление отменено")
    staging = code_dir.with_name(code_dir.name + ".new")
    previous = code_dir.with_name(code_dir.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _extract(archive, staging)
        _validate(staging / "spoke", release, shell_api if shell_api is not None else installed_shell_api())
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(previous, ignore_errors=True)
    if code_dir.exists():
        code_dir.rename(previous)
    staging.rename(code_dir)
    shutil.rmtree(previous, ignore_errors=True)


def reset(code_dir: Path = CODE_DIR) -> None:
    shutil.rmtree(code_dir, ignore_errors=True)


def restart() -> bool:
    resources = os.environ.get("RESOURCEPATH")
    if not resources:
        return False
    bundle = Path(resources).parents[1]
    subprocess.Popen(["/bin/sh", "-c", f"sleep 1.5; open -a '{bundle}'"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
