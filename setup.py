import sys

from setuptools import setup

sys.setrecursionlimit(20000)

OPTIONS = {
    "iconfile": "packaging/Spoke.icns",
    "excludes": ["mlx", "torch", "torchgen", "torchaudio", "torchvision", "sympy", "tkinter", "matplotlib", "pytest"],
    "packages": ["spoke", "mlx_whisper", "rumps", "pynput", "sounddevice", "_sounddevice_data", "numpy", "tiktoken", "huggingface_hub",
                 "tqdm", "more_itertools", "objc", "AppKit", "Foundation", "Quartz", "ApplicationServices"],
    "resources": ["packaging/model"],
    "plist": {
        "CFBundleName": "Spoke",
        "CFBundleDisplayName": "Spoke",
        "CFBundleIdentifier": "local.spoke.app",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,
        "LSMinimumSystemVersion": "13.0",
        "NSMicrophoneUsageDescription": "Spoke записывает вашу речь, чтобы превратить её в текст. Звук обрабатывается на этом Mac и никуда не отправляется.",
    },
}

setup(app=["spoke_main.py"], name="Spoke", options={"py2app": OPTIONS}, setup_requires=["py2app"])
