import sys
from pathlib import Path

UPDATED_CODE = Path.home() / "Library" / "Application Support" / "Spoke" / "code"


def load_main():
    if (UPDATED_CODE / "spoke" / "__init__.py").exists():
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
