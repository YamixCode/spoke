"""Sign nested macOS code before sealing the app for Developer ID distribution."""
from pathlib import Path
import subprocess
import sys

MACHO = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
BUNDLES = {".app", ".appex", ".framework", ".xpc"}


def is_macho(path: Path) -> bool:
    with path.open("rb") as binary:
        return binary.read(4) in MACHO


def sign(app: Path, identity: str, entitlements: Path) -> None:
    command = ["/usr/bin/codesign", "--force", "--sign", identity, "--timestamp", "--options", "runtime"]
    entries = [path for path in (app / "Contents").rglob("*") if not path.is_symlink()]
    for path in entries:
        if path.is_file() and is_macho(path):
            subprocess.run([*command, str(path)], check=True)
    bundles = [path for path in entries if path.is_dir() and path.suffix in BUNDLES]
    for path in sorted(bundles, key=lambda path: len(path.parts), reverse=True):
        subprocess.run([*command, str(path)], check=True)
    subprocess.run([*command, "--entitlements", str(entitlements), str(app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)


if __name__ == "__main__":
    sign(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
