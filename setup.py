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
    "packages": ["spoke", "rumps", "sounddevice", "_sounddevice_data", "numpy", "tiktoken", "certifi",
                 "objc", "AppKit", "Foundation", "Quartz", "ApplicationServices"],
    "plist": {
        "CFBundleName": "Spoke",
        "CFBundleDisplayName": "Spoke",
        "CFBundleIdentifier": "local.spoke.app",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": SHELL_API,
        "LSUIElement": True,
        "LSMinimumSystemVersion": "26.0",
        "NSMicrophoneUsageDescription": "Spoke записывает вашу речь для диктовки. При выборе облачного режима звук отправляется в ElevenLabs для распознавания.",
    },
}

setup(app=["spoke_main.py"], name="Spoke", data_files=[("terminal", ["spoke/terminal.zsh", "spoke/terminal_editor.py"])],
      options={"py2app": OPTIONS}, setup_requires=["py2app"])
