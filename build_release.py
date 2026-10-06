"""Build and audit a distributable without ever reading the user's state directory."""
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from app_runtime import VERSION
from build_assets import main as build_assets

ROOT = Path(__file__).resolve().parent
RUNTIME_PACKAGES = ("requests", "urllib3", "certifi", "charset-normalizer", "idna", "markdown-it-py", "mdurl")
SOURCE_FILES = (
    "app_runtime.py", "entry.py", "desktop.py", "publish.py", "auth_worker.py",
    "build_assets.py", "build_release.py", "publisher.spec", "requirements.txt",
    "build-requirements.txt", "README.md", "使用说明.txt", "open-publisher.cmd",
    "icviews.cmd", "publish-article.cmd", "setup.cmd", "test_publish.py",
    "test_diagnostics.py", "test_auth_worker.py", "test_distribution.py",
    "LICENSE", ".gitignore",
)


def zip_directory(directory, output):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, Path(directory.name) / path.relative_to(directory))


def license_notices(bundle):
    notices = ["Third-party components bundled with ICViews Publisher\n"]
    for name in (*RUNTIME_PACKAGES, "setuptools", "pyinstaller"):
        dist = metadata.distribution(name)
        notices.append(f"\n{'=' * 72}\n{name} {dist.version}\n")
        license_files = [f for f in dist.files or [] if any(word in f.name.lower() for word in ("license", "copying", "notice"))
                         and dist.locate_file(f).is_file()]
        if not license_files:
            raise RuntimeError(f"Missing license files: {name}")
        for path in license_files:
            notices.append("\n" + str(path) + "\n")
            notices.append(dist.locate_file(path).read_text(encoding="utf-8", errors="replace"))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    notices.append("\nPython runtime\n" + python_license.read_text(encoding="utf-8"))
    for license_file in sorted((bundle / "_internal").rglob("license.terms")):
        notices.append("\nTcl/Tk: " + license_file.name + "\n" + license_file.read_text(encoding="utf-8", errors="replace"))
    notices.append("\nLucide icons\n" + (ROOT / "assets" / "LUCIDE-LICENSE.txt").read_text(encoding="utf-8"))
    (bundle / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")


def audit_bundle(bundle):
    forbidden = {"credentials.json", "last-error.json", "last-login-schema.json", "images.json",
                 "publisher.lock", "preview.html", "icviews-image-map.json", "uploaded.html"}
    files = [p for p in bundle.rglob("*") if p.is_file()]
    bad = [p.relative_to(bundle).as_posix() for p in files if p.name in forbidden or ".venv" in p.parts or "__pycache__" in p.parts]
    if bad:
        raise RuntimeError(f"Private or development data found in bundle: {bad}")
    return {"fileCount": len(files), "privateStateFiles": 0, "defaultArticle": None}


def main():
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Build on 64-bit Windows.")
    build_assets()
    build_root = ROOT / "build" / time.strftime("%Y%m%d-%H%M%S")
    build_root.mkdir(parents=True, exist_ok=False)
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(build_root / "dist"), "--workpath", str(build_root / "work"),
                    str(ROOT / "publisher.spec")], cwd=ROOT, check=True)
    built = build_root / "dist" / "ICViewsPublisher"
    for name in ("使用说明.txt", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, built / name)
    shutil.copytree(ROOT / "examples", built / "examples")
    shutil.copytree(ROOT / "docs", built / "docs")
    # Icons and their license are also available beside the executable for inspection.
    (built / "assets").mkdir()
    shutil.copy2(ROOT / "assets" / "LUCIDE-LICENSE.txt", built / "assets" / "LUCIDE-LICENSE.txt")
    license_notices(built)

    # Verify the executable in a moved path with spaces, Unicode, and a fresh LOCALAPPDATA.
    moved = build_root / "portable smoke 测试" / "ICViewsPublisher"
    shutil.copytree(built, moved)
    profile = build_root / "clean-profile"
    env = dict(os.environ, LOCALAPPDATA=str(profile), PYTHONUTF8="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    check = subprocess.run([str(moved / "icviews-worker.exe"), "--worker", "publish", "check",
                            str(moved / "examples" / "示例文章.md")], cwd=moved, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    if check.returncode or not (profile / "ICViewsPublisher" / "preview.html").exists():
        raise RuntimeError("Frozen offline check failed: " + check.stdout + check.stderr)
    auth = subprocess.run([str(moved / "icviews-worker.exe"), "--worker", "auth", "login"],
                          input='{"mobile":"","code":""}', cwd=moved, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    if auth.returncode != 1 or json.loads(auth.stdout).get("ok") is not False:
        raise RuntimeError("Frozen auth validation failed.")
    if (profile / "ICViewsPublisher" / "credentials.json").exists():
        raise RuntimeError("Offline test unexpectedly created credentials.")

    manifest = {"version": VERSION, "platform": "windows-x64", "python": sys.version.split()[0],
                "dependencies": {p: metadata.version(p) for p in RUNTIME_PACKAGES},
                "tests": {"relocatedOfflineCheck": True, "authValidationWithoutNetwork": True},
                "audit": audit_bundle(built)}
    (built / "BUILD_INFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    portable = release / f"ICViewsPublisher-{VERSION}-windows-x64.zip"
    zip_directory(built, portable)

    source = build_root / "ICViewsPublisher-source"
    source.mkdir()
    for name in SOURCE_FILES:
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / "assets", source / "assets")
    shutil.copytree(ROOT / "examples", source / "examples")
    shutil.copytree(ROOT / "docs", source / "docs")
    shutil.copytree(ROOT / ".github", source / ".github")
    audit_bundle(source)
    source_zip = release / f"ICViewsPublisher-{VERSION}-source.zip"
    zip_directory(source, source_zip)
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in (portable, source_zip)]
    (release / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
    (release / "build-result.json").write_text(json.dumps({"bundle": str(built), "smokeBundle": str(moved),
                                                         "manifest": manifest}, indent=2), encoding="utf-8")
    print("PORTABLE:", portable)
    print("SOURCE:", source_zip)
    print("SMOKE_BUNDLE:", moved)


if __name__ == "__main__":
    main()
