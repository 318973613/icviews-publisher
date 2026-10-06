"""Paths and worker dispatch shared by source and frozen distributions."""
from pathlib import Path
import sys

VERSION = "1.3.0"
APP_NAME = "ICViews Publisher"
ROOT = Path(__file__).resolve().parent


def worker_command(channel, *arguments):
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("icviews-worker.exe")
        return [str(executable), "--worker", channel, *arguments]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), str(ROOT / "entry.py"), "--worker", channel, *arguments]


def documentation_path():
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
    return base / "使用说明.txt"
