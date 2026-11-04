"""Build the source-mode distribution (4.4.0+):

    build\ScreenSplitter_Source_Dist\      a shallow git clone of one pushed ref
        python\                            portable Python 3.11 with the pinned requirements
        git\                               MinGit, for update.bat
        VLC\                               copied from .\VLC when present
        player.py, web_server.py, ...      the checkout itself (update.bat keeps it current)

Usage:
    venv\Scripts\python.exe build_source_release.py [--ref main] [--installer]

Everything downloaded is cached in .cache\ (ignored by git). The result is verified with
the portable interpreter: imports, the Player's offscreen Qt check and the unit tests.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
TOOLS = CACHE / "tools"
WHEELS = CACHE / "wheels"
DIST = ROOT / "build" / "ScreenSplitter_Source_Dist"
REPO_URL = "https://github.com/mount0603-star/ScreenSplitter.git"
PYTHON_VERSION = "3.11.9"
MINGIT_VERSION = "2.56.0.4"
INNO = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")

DOWNLOADS = {
    f"python-{PYTHON_VERSION}-embed-amd64.zip": (
        f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
        "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"),
    "get-pip.py": ("https://bootstrap.pypa.io/get-pip.py", None),
    f"MinGit-{MINGIT_VERSION}-64-bit.zip": (
        f"https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-{MINGIT_VERSION}-64-bit.zip",
        "e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877"),
}

# Explicit search path and NO "import site": with site enabled the embeddable interpreter
# also adds the current user's %APPDATA%\Python\Python311\site-packages, so whatever that
# Windows account has installed would leak into the kiosk runtime. pywin32's .pth bootstrap
# is replaced by listing its folders here and by copying its DLLs next to python.exe.
PTH_LINES = ["python311.zip", ".", "Lib\\site-packages", "Lib\\site-packages\\win32",
             "Lib\\site-packages\\win32\\lib", "Lib\\site-packages\\Pythonwin"]
IMPORT_CHECK = ("import sys; "
                "assert 'site' not in sys.modules, 'site must stay disabled'; "
                "assert not any('roaming' in p.lower() and 'python' in p.lower() for p in sys.path), sys.path; "
                "import PySide6.QtWidgets, PySide6.QtWebEngineWidgets, PySide6.QtWebEngineCore, "
                "flask, webview, psutil, win32api, win32gui, win32event, win32process, win32security, "
                "pythonnet, clr_loader; print('imports ok, isolated from user site-packages')")


def log(message):
    print(message, flush=True)


def run(cmd, cwd=None, env=None, check=True, capture=False):
    log("  $ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env, check=check,
                          capture_output=capture, text=True, encoding="utf-8", errors="replace")


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_tools():
    TOOLS.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in DOWNLOADS.items():
        target = TOOLS / name
        if not target.is_file() or target.stat().st_size == 0:
            log(f"Downloading {name}")
            with urllib.request.urlopen(url, timeout=120) as response, open(target, "wb") as out:
                shutil.copyfileobj(response, out)
        if expected and sha256(target) != expected:
            raise RuntimeError(f"{name}: SHA-256 mismatch; delete it from {TOOLS} and retry")
        log(f"Tool ready: {name} ({target.stat().st_size // 1024} KB)")


def _remove_tree(path):
    """rmtree that also removes read-only files (git marks its pack files read-only)."""
    def force_writable(func, failed_path, _exc_info):
        os.chmod(failed_path, stat.S_IWRITE)
        func(failed_path)
    shutil.rmtree(path, onerror=force_writable)


def clone_checkout(ref):
    if DIST.exists():
        _remove_tree(DIST)
    DIST.parent.mkdir(parents=True, exist_ok=True)
    log(f"Cloning {REPO_URL} ({ref}) -> {DIST}")
    run(["git", "clone", "--depth", "1", "--branch", ref, REPO_URL, DIST], cwd=ROOT)
    commit = run(["git", "rev-parse", "HEAD"], cwd=DIST, capture=True).stdout.strip()
    # Line endings and long paths must not make the installed checkout look modified.
    run(["git", "config", "core.autocrlf", "true"], cwd=DIST)
    run(["git", "config", "core.longpaths", "true"], cwd=DIST)
    local = run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture=True).stdout.strip()
    if local != commit:
        log(f"NOTE: local HEAD {local[:12]} differs from the cloned {ref} ({commit[:12]}); the dist ships the pushed ref.")
    return commit


def build_wheels():
    """Wheels for every requirement pinned by the shipped checkout, built once with the dev venv."""
    WHEELS.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "wheel", "-r", DIST / "requirements.txt", "-w", WHEELS, "--no-deps", "-q"], cwd=ROOT)


def copy_pywin32_dlls(target):
    """pywin32 normally finds pywintypes311.dll through its .pth bootstrap; without site the
    DLLs must sit beside python.exe. update.bat repeats this after a pip install."""
    source = target / "Lib" / "site-packages" / "pywin32_system32"
    names = [dll.name for dll in source.glob("*.dll")]
    for name in names:
        shutil.copy2(source / name, target / name)
    log(f"  pywin32 DLLs copied beside python.exe: {names}")


def build_portable_python(target):
    log(f"Portable Python -> {target}")
    with zipfile.ZipFile(TOOLS / f"python-{PYTHON_VERSION}-embed-amd64.zip") as archive:
        archive.extractall(target)
    pth = next(target.glob("python3*._pth"))
    pth.write_text("\n".join(PTH_LINES) + "\n", encoding="utf-8")
    python = target / "python.exe"
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1")
    run([python, TOOLS / "get-pip.py", "--no-warn-script-location", "-q"], cwd=target, env=env)
    # Install exactly what the shipped checkout pins (the clone's requirements.txt).
    run([python, "-m", "pip", "install", "--no-index", "--find-links", WHEELS, "--no-warn-script-location",
         "-q", "-r", target.parent / "requirements.txt"], cwd=target, env=env)
    run([python, "-m", "pip", "check"], cwd=target, env=env)
    copy_pywin32_dlls(target)
    for cache in target.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def unpack_mingit(target):
    log(f"MinGit -> {target}")
    with zipfile.ZipFile(TOOLS / f"MinGit-{MINGIT_VERSION}-64-bit.zip") as archive:
        archive.extractall(target)
    if not (target / "cmd" / "git.exe").is_file():
        raise RuntimeError("MinGit archive layout unexpected: cmd\\git.exe missing")


def verify(dist):
    python = dist / "python" / "python.exe"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen")
    log("Verify: imports")
    run([python, "-c", IMPORT_CHECK], cwd=dist, env=env)
    log("Verify: Player offscreen Qt check")
    with tempfile.TemporaryDirectory(prefix="ScreenSplitter-source-check-") as temporary:
        output = Path(temporary) / "result.json"
        run([python, "player.py", "--runtime-check-output", output], cwd=dist, env=env)
        result = json.loads(output.read_text(encoding="utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Player runtime check failed: {result}")
        log(f"  Player runtime verified: {result}")
    log("Verify: unit tests with the portable interpreter")
    tests = run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=dist, env=env, check=False, capture=True)
    tail = "\n".join((tests.stderr or tests.stdout).splitlines()[-4:])
    log("  " + tail.replace("\n", "\n  "))
    if tests.returncode != 0:
        raise RuntimeError("unit tests failed with the portable interpreter")
    log("Verify: checkout is clean (python/, git/, VLC/ must be ignored)")
    status = run(["git", "status", "--porcelain"], cwd=dist, capture=True).stdout.strip()
    if status:
        raise RuntimeError("dist checkout is not clean:\n" + status)
    return tail


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="main", help="branch or tag to ship (must be pushed to GitHub)")
    parser.add_argument("--installer", action="store_true", help="also compile setup_source.iss with Inno Setup")
    args = parser.parse_args()
    os.chdir(ROOT)

    download_tools()
    commit = clone_checkout(args.ref)
    build_wheels()
    build_portable_python(DIST / "python")
    unpack_mingit(DIST / "git")
    if (ROOT / "VLC").is_dir():
        log("Copying VLC")
        shutil.copytree(ROOT / "VLC", DIST / "VLC", dirs_exist_ok=True)
    else:
        log("NOTE: .\\VLC not found; the dist has no bundled VLC (only needed for video_backend=vlc)")
    tests_tail = verify(DIST)
    info = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "ref": args.ref, "commit": commit,
        "version": (DIST / "VERSION").read_text(encoding="utf-8").strip() if (DIST / "VERSION").is_file() else "unknown",
        "python": PYTHON_VERSION, "mingit": MINGIT_VERSION,
        "requirements_sha256": sha256(DIST / "requirements.txt"),
        "tests": tests_tail,
    }
    (DIST / "python" / "ScreenSplitter_build.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    log(f"Source dist ready: {DIST}\n{json.dumps(info, indent=2)}")

    if args.installer:
        if not INNO.is_file():
            raise RuntimeError(f"Inno Setup not found at {INNO}")
        run([INNO, ROOT / "setup_source.iss"], cwd=ROOT)
        log(f"Installer: {ROOT / 'build' / 'ScreenSplitter_Setup_Source.exe'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log(f"BUILD FAILED: {error}")
        raise
