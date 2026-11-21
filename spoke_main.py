import os
import plistlib
import sys
from pathlib import Path

UPDATED_CODE = Path.home() / "Library" / "Application Support" / "Spoke" / "code"


def load_main():
    resources = os.environ.get("RESOURCEPATH")
    signed_distribution = False
    if resources:
        plist = Path(resources).parent / "Info.plist"
        signed_distribution = plist.is_file() and plistlib.loads(plist.read_bytes()).get("SpokeSignedDistribution", False)
    if not signed_distribution and (UPDATED_CODE / "spoke" / "__init__.py").exists():
        sys.path.insert(0, str(UPDATED_CODE))
        try:
            import spoke.app
            from spoke.__main__ import main
            return main
        except Exception:
            sys.path.remove(str(UPDATED_CODE))
            for name in [name for name in sys.modules if name == "spoke" or name.startswith("spoke.")]:
                del sys.modules[name]
    from spoke.__main__ import main
    return main


load_main()()
