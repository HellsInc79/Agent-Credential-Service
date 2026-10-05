"""Install portable Windows media tools into the local project."""

import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME = _ROOT / "tools" / "runtime"
_JOBS = {}
_LOCK = threading.RLock()
_MAX_DOWNLOAD = {"ffmpeg": 700 * 1024 * 1024, "blender": 2_000 * 1024 * 1024}
_MAX_UNPACKED = {"ffmpeg": 2_000 * 1024 * 1024, "blender": 5_000 * 1024 * 1024}
_SOURCES = {
    "ffmpeg": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "blender_page": "https://www.blender.org/download/",
}


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _update(job_id, **values):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(values, updated_at=_utc())


def list_installation_jobs():
    with _LOCK:
        return [dict(job) for job in sorted(_JOBS.values(), key=lambda row: row["created_at"], reverse=True)]


def _blender_zip_url():
    request = Request(_SOURCES["blender_page"], headers={"User-Agent": "LocalAgentPlatform/1.0"})
    with urlopen(request, timeout=15) as response:
        page_url = response.geturl()
        if urlparse(page_url).hostname not in {"www.blender.org", "blender.org"}:
            raise RuntimeError("The Blender download page redirected to an unexpected site.")
        html = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    # Blender's download page links to its own redirect URL, which forwards
    # to download.blender.org. Accept that official link as well as a direct
    # download link because the site's page markup can use either form.
    matches = re.findall(
        r"https://(?:www\.blender\.org/download|download\.blender\.org)/release/"
        r"(Blender[0-9.]+)/(blender-[0-9.]+-windows-x64\.zip)/?",
        html,
    )
    if not matches:
        raise RuntimeError("Could not find the official Windows portable Blender download on Blender.org.")
    version_folder, filename = matches[0]
    return f"https://download.blender.org/release/{version_folder}/{filename}"


def _download(job_id, url, path, tool):
    if urlparse(url).scheme != "https":
        raise RuntimeError("The download source must use a secure HTTPS connection.")
    request = Request(url, headers={"User-Agent": "LocalAgentPlatform/1.0"})
    with urlopen(request, timeout=60) as response:
        final_host = (urlparse(response.geturl()).hostname or "").lower()
        allowed = {"www.gyan.dev", "gyan.dev", "download.blender.org"}
        if final_host not in allowed:
            raise RuntimeError("The download redirected to an unexpected site.")
        total = int(response.headers.get("Content-Length") or 0)
        if total > _MAX_DOWNLOAD[tool]:
            raise RuntimeError("The download is larger than the safety limit for this tool.")
        downloaded = 0
        with path.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > _MAX_DOWNLOAD[tool]:
                    raise RuntimeError("The download exceeded the safety limit for this tool.")
                output.write(chunk)
                percent = round(downloaded * 100 / total) if total else None
                _update(job_id, status="downloading", progress=percent, bytes_downloaded=downloaded, bytes_total=total)
    if path.stat().st_size < 1024:
        raise RuntimeError("The download was empty or incomplete.")


def _safe_extract(zip_path, destination, tool):
    destination.mkdir(parents=True, exist_ok=True)
    unpacked_size = 0
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            raw = info.filename.replace("\\", "/")
            relative = PurePosixPath(raw)
            if relative.is_absolute() or ".." in relative.parts or (relative.parts and ":" in relative.parts[0]):
                raise RuntimeError("The downloaded archive contains an unsafe file path and was not installed.")
            # Refuse archive entries marked as symbolic links.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("The downloaded archive contains an unsupported link entry.")
            unpacked_size += info.file_size
            if unpacked_size > _MAX_UNPACKED[tool]:
                raise RuntimeError("The extracted files exceed the safety limit for this tool.")
            target = (destination / Path(*relative.parts)).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError("The downloaded archive contains an unsafe file path and was not installed.")
        archive.extractall(destination)


def _find_binaries(tool, extracted):
    if tool == "ffmpeg":
        ffmpeg_files = list(extracted.rglob("ffmpeg.exe"))
        for ffmpeg in ffmpeg_files:
            ffprobe = ffmpeg.with_name("ffprobe.exe")
            if ffprobe.is_file():
                return {"ffmpeg": ffmpeg, "ffprobe": ffprobe}
        raise RuntimeError("The FFmpeg archive did not contain both ffmpeg.exe and ffprobe.exe.")
    blender = next(extracted.rglob("blender.exe"), None)
    if not blender or not blender.is_file():
        raise RuntimeError("The Blender archive did not contain blender.exe.")
    return {"blender": blender}


def _record_install(tool, target, binaries):
    marker = _RUNTIME / "installed-tools.json"
    try:
        info = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    except (OSError, ValueError):
        info = {}
    info[tool] = {name: str(path.relative_to(_RUNTIME)).replace("\\", "/") for name, path in binaries.items()}
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(info, indent=2), encoding="utf-8")
    os.replace(temporary, marker)


def _install_worker(job_id, tool):
    _RUNTIME.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{tool}-install-", dir=_RUNTIME))
    target = _RUNTIME / tool
    backup = _RUNTIME / f".{tool}-previous-{uuid.uuid4().hex[:8]}"
    moved_old = False
    try:
        _update(job_id, status="resolving", message="Finding the official download…", progress=None)
        url = _SOURCES["ffmpeg"] if tool == "ffmpeg" else _blender_zip_url()
        archive = stage / f"{tool}.zip"
        _download(job_id, url, archive, tool)
        _update(job_id, status="extracting", message="Checking and unpacking the Windows files…", progress=None)
        extracted = stage / "unpacked"
        _safe_extract(archive, extracted, tool)
        binaries = _find_binaries(tool, extracted)
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(extracted, target)
        installed = {name: target / path.relative_to(extracted) for name, path in binaries.items()}
        _record_install(tool, target, installed)
        if moved_old:
            shutil.rmtree(backup, ignore_errors=True)
        _update(job_id, status="complete", progress=100, message="Installed in this project and ready.", installed_paths={name: str(path) for name, path in installed.items()}, error="")
    except Exception as exc:
        if moved_old and backup.exists():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(backup, target)
        _update(job_id, status="error", message="Installation stopped.", error=str(exc)[:1000])
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def start_installation(tool):
    if tool not in {"ffmpeg", "blender"}:
        raise ValueError("Choose FFmpeg or Blender to install.")
    job_id = uuid.uuid4().hex
    now = _utc()
    job = {"id": job_id, "tool": tool, "status": "queued", "progress": 0, "bytes_downloaded": 0, "bytes_total": 0, "message": "Queued for local installation…", "error": "", "created_at": now, "updated_at": now}
    with _LOCK:
        active = next((row for row in _JOBS.values() if row["tool"] == tool and row["status"] in {"queued", "resolving", "downloading", "extracting"}), None)
        if active:
            return dict(active)
        _JOBS[job_id] = job
    threading.Thread(target=_install_worker, args=(job_id, tool), daemon=True, name=f"install-{tool}-{job_id[:8]}").start()
    return dict(job)
