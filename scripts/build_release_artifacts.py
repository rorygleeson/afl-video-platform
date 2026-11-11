#!/usr/bin/env python3
"""Deterministic, content-addressed release packages. No AWS writes.

Google's live dependency-heavy ZIP and FFmpeg layer are deliberately NOT
rebuilt here until dependency and binary hashes are pinned and verified.
"""
import hashlib
import subprocess
import sys
import tempfile
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "release"
SOURCES = {
    "api": {
        "handler.py": "src/api/handler.py",
        "video_prompts.py": "src/api/video_prompts.py",
    },
    "image-worker": {"handler.py": "src/image_worker/handler.py", "google_direct.py": "src/image_worker/google_direct.py"},
    "assembly-worker": {"handler.py": "src/assembly_worker/handler.py"},
    "legacy-video-worker": {"handler.py": "src/video_worker/handler.py"},
}
def build():
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"format": 1, "artifacts": {}, "limitations": [
        "Google worker: preserve verified live artifact; dependency lock pending",
        "FFmpeg layer: preserve verified live artifact; binary provenance pending",
    ]}
    # Install only the Google image-worker's pinned auth dependencies.
    # ZIP metadata, paths and bytes remain deterministic across builds.
    with tempfile.TemporaryDirectory(prefix="afl-image-auth-") as td:
        target = Path(td)
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--no-compile",
            "--disable-pip-version-check", "--only-binary=:all:",
            "--target", str(target), "-r",
            str(ROOT / "src/image_worker/requirements.txt"),
        ], check=True, stdout=subprocess.DEVNULL)
        vendor = {
            str(file.relative_to(target)).replace("\\\\", "/"): file
            for file in target.rglob("*")
            if file.is_file() and not any(
                name.endswith(".dist-info") or name.endswith(".data")
                or name == "__pycache__" for name in file.relative_to(target).parts
            ) and file.suffix not in (".pyc", ".pyo")
        }
        for name, entries in SOURCES.items():
            archive = OUT / (name + ".zip")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for dest, source in sorted(entries.items()):
                    data = (ROOT / source).read_bytes()
                    info = zipfile.ZipInfo(dest, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    info.create_system = 3
                    z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                if name == "image-worker":
                    for dest, src in sorted(vendor.items()):
                        data = src.read_bytes()
                        info = zipfile.ZipInfo(dest, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o644 << 16
                        info.create_system = 3
                        z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            data = archive.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            result["artifacts"][name] = {
                "local_file": str(archive.relative_to(ROOT)),
                "sha256_hex": sha,
                "s3_key": f"releases/sha256/{sha}/{name}.zip",
                "bytes": len(data),
            }
    (OUT / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
if __name__ == "__main__":
    build()
