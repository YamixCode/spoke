"""Собрать архив с кодом для обновления: spoke-code-X.Y.Z.zip и файл контрольной суммы рядом."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "spoke" / "__init__.py").read_text(encoding="utf-8")).group(1)


def build_native(required: bool) -> None:
    if sys.platform == "darwin" and shutil.which("swiftc"):
        subprocess.run([str(ROOT / "scripts" / "build_native.sh")], check=True)
    elif required:
        raise SystemExit("swiftc не найден: библиотеку окна настроек нужно собирать на macOS")


def build(destination: Path, require_native: bool = False) -> Path:
    build_native(require_native)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"spoke-code-{version()}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "spoke").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.name != ".DS_Store":
                bundle.write(path, path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(version())
    else:
        arguments = [argument for argument in sys.argv[1:] if not argument.startswith("--")]
        print(build(Path(arguments[0] if arguments else "dist"), "--require-native" in sys.argv))
