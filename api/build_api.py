"""Builds the results API deployment package.

1. Writes src/catalog.json (questions, weights, areas, bands, profile options, scenes) from catalogue.py, so the
   API scores exactly like the app.
2. Downloads the wheels pinned with SHA-256 hashes in src/requirements.txt (Linux x86_64, CPython 3.12) and
   unpacks them into .python_packages/lib/site-packages, the layout Azure Functions loads without a remote build.
3. Writes a reproducible dist/api.zip (sorted entries, fixed timestamps and permissions) and prints its SHA-256.

Usage: python build_api.py [--catalog-only]
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.dirname(HERE)
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
sys.path.insert(0, FILES)

import catalogue as c  # noqa: E402

SOURCES = ["function_app.py", "service.py", "core.py", "guard.py", "store.py", "relay.py", "dash.html",
           "catalog.json", "host.json", "requirements.txt"]
EPOCH = (1980, 1, 1, 0, 0, 0)


def app_version():
    with open(os.path.join(FILES, "app", "build_app.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"', f.read(), re.M).group(1)


def catalog():
    keys = {"P-02": "sector", "P-03": "size", "P-04": "role"}
    return {
        "version": app_version(),
        "questions": [{"code": q["code"], "area": q["area"], "weight": q["weight"], "critical": q["critical"],
                       "short": q["short"], "gap": q["gap"]} for q in c.QUESTIONS],
        "areas": [{"code": a["code"], "name": a["name"], "weight": round(a["weight"] * 100)} for a in c.AREAS],
        "bands": [{"lo": lo, "name": name} for lo, name, _ in c.BANDS],
        "profile": {keys[p["code"]]: p["options"] for p in c.PROFILE if p["code"] in keys},
        "scenarios": [{"id": s["id"], "title": s["title"], "codes": s["codes"]} for s in c.SCENARIOS],
    }


def write_catalog():
    s = json.dumps(catalog(), ensure_ascii=False, indent=1) + "\n"
    with open(os.path.join(SRC, "catalog.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    return s


def vendor(pkg_dir):
    wheels = os.path.join(BUILD, "wheels")
    shutil.rmtree(wheels, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
                    "--require-hashes", "--no-deps", "--only-binary=:all:", "--platform", "manylinux2014_x86_64",
                    "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
                    "-r", os.path.join(SRC, "requirements.txt"), "-d", wheels], check=True)
    site = os.path.join(pkg_dir, ".python_packages", "lib", "site-packages")
    os.makedirs(site)
    names = sorted(os.listdir(wheels))
    assert len(names) == 3 and all(n.endswith(".whl") for n in names), names
    for n in names:
        with zipfile.ZipFile(os.path.join(wheels, n)) as z:
            for info in z.infolist():
                parts = info.filename.split("/")
                assert not info.filename.startswith("/") and ".." not in parts and "\\" not in info.filename
                assert not any(p.endswith(".data") for p in parts[:1]), "wheel .data directories are not supported"
            z.extractall(site)
    return names


def build():
    write_catalog()
    shutil.rmtree(BUILD, ignore_errors=True)
    pkg = os.path.join(BUILD, "pkg")
    os.makedirs(pkg)
    for n in SOURCES:
        shutil.copyfile(os.path.join(SRC, n), os.path.join(pkg, n))
    wheels = vendor(pkg)
    files = []
    for root, dirs, fs in os.walk(pkg):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(fs):
            if f.endswith(".pyc"):
                continue
            full = os.path.join(root, f)
            files.append((os.path.relpath(full, pkg).replace(os.sep, "/"), full))
    files.sort()
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "api.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, full in files:
            info = zipfile.ZipInfo(arc, EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as fh:
                z.writestr(info, fh.read(), compresslevel=9)
    with open(out, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print("wheels:", ", ".join(wheels))
    print("files:", len(files), "zip:", os.path.getsize(out), "bytes")
    print("sha256:", digest)
    return out, digest


if __name__ == "__main__":
    if "--catalog-only" in sys.argv:
        write_catalog()
        print("catalog.json written, version", app_version())
    else:
        build()
