import re
import sys
from pathlib import Path

from setuptools import setup

sys.setrecursionlimit(20000)

SOURCE = Path(__file__).parent.joinpath("spoke", "__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', SOURCE).group(1)
SHELL_API = re.search(r"SHELL_API\s*=\s*(\d+)", SOURCE).group(1)

OPTIONS = {
    "iconfile": "packaging/Spoke.icns",
    "excludes": ["mlx", "torch", "torchgen", "torchaudio", "torchvision", "sympy", "tkinter", "matplotlib", "pytest"],
    "packages": ["spoke", "mlx_whisper", "rumps", "sounddevice", "_sounddevice_data", "numpy", "tiktoken", "huggingface_hub", "certifi",
                 "tqdm", "more_itertools", "objc", "AppKit", "Foundation", "Quartz", "ApplicationServices"],
    "resources": ["packaging/model"],
    "plist": {
        "CFBundleName": "Spoke",
        "CFBundleDisplayName": "Spoke",
        "CFBundleIdentifier": "local.spoke.app",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": SHELL_API,
        "LSUIElement": True,
        "LSMinimumSystemVersion": "13.0",
        "NSMicrophoneUsageDescription": "Spoke записывает вашу речь, чтобы превратить её в текст. Звук обрабатывается на этом Mac и никуда не отправляется.",
    },
}

setup(app=["spoke_main.py"], name="Spoke", options={"py2app": OPTIONS}, setup_requires=["py2app"])
