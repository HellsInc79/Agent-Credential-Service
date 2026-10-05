# Local video rough-cut rendering and Blender starter helpers.

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_ALLOWED = _VIDEO_EXT | _IMAGE_EXT | _AUDIO_EXT
_MAX_ASSET_BYTES = 250 * 1024 * 1024
_MAX_TOTAL_SECONDS = 3600
_UUID = re.compile(r"^[0-9a-f]{32}$")

class VideoStudioError(Exception):
    def __init__(self, detail, status_code=400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _root():
    configured = Path(os.getenv("VIDEO_STUDIO_DIR", "video_studio"))
    base = Path(__file__).resolve().parent.parent
    return (configured if configured.is_absolute() else base / configured).resolve()


def _tool(name, env_name):
    configured = os.getenv(env_name, "").strip()
    if configured and Path(configured).is_file():
        return configured
    base = Path(__file__).resolve().parent.parent
    marker = base / "tools" / "runtime" / "installed-tools.json"
    if marker.is_file():
        try:
            installed = json.loads(marker.read_text(encoding="utf-8"))
            relative = installed.get("ffmpeg" if name in {"ffmpeg", "ffprobe"} else name, {}).get(name)
            candidate = (base / "tools" / "runtime" / relative).resolve() if relative else None
            if candidate and candidate.is_file():
                return str(candidate)
        except (OSError, ValueError, TypeError):
            pass
    bundled = {
        "ffmpeg": [
            base / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe",
            base / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ],
        "ffprobe": [
            base / "tools" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe",
            base / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
        ],
        "blender": [base / "tools" / "runtime" / "blender" / "blender.exe"],
    }
    for candidate in bundled.get(name, []):
        if candidate.is_file():
            return str(candidate)
    if name == "blender":
        installed_blender = base / "tools" / "runtime" / "blender"
        if installed_blender.is_dir():
            marker_path = installed_blender / ".app-tool-path"
            if marker_path.is_file():
                candidate = (installed_blender / marker_path.read_text(encoding="utf-8").strip()).resolve()
                if candidate.is_relative_to(installed_blender.resolve()) and candidate.is_file():
                    return str(candidate)
    return shutil.which(name)


def tool_status():
    ffmpeg = _tool("ffmpeg", "FFMPEG_BINARY")
    ffprobe = _tool("ffprobe", "FFPROBE_BINARY")
    blender = _tool("blender", "BLENDER_BINARY")
    return {"ffmpeg_available": bool(ffmpeg), "ffprobe_available": bool(ffprobe), "blender_available": bool(blender), "ffmpeg": ffmpeg or "", "ffprobe": ffprobe or "", "blender": blender or ""}


def _safe_filename(value):
    name = Path((value or "media").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:140]
    return name or "media"


def _asset_dir(asset_id):
    return _root() / "assets" / asset_id if _UUID.fullmatch(asset_id or "") else None


async def save_video_asset(filename, stream):
    filename = _safe_filename(filename)
    extension = Path(filename).suffix.lower()
    if extension not in _ALLOWED:
        raise VideoStudioError("Choose a video, picture, or audio file in a supported format.", 415)
    kind = "video" if extension in _VIDEO_EXT else "image" if extension in _IMAGE_EXT else "audio"
    asset_id = uuid.uuid4().hex
    folder = _root() / "assets" / asset_id
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / ("source" + extension)
    total = 0
    try:
        with path.open("wb") as output:
            async for chunk in stream:
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_ASSET_BYTES:
                    raise VideoStudioError("Each video-studio file must be 250 MB or smaller.", 413)
                output.write(chunk)
        if not total:
            raise VideoStudioError("The selected file is empty.", 422)
        record = {"id": asset_id, "filename": filename, "extension": extension, "kind": kind, "size": total, "created_at": datetime.now(timezone.utc).isoformat()}
        (folder / "asset.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _read_asset(asset_id):
    folder = _asset_dir(asset_id)
    if not folder or not folder.is_dir():
        return None
    try:
        record = json.loads((folder / "asset.json").read_text(encoding="utf-8"))
        path = folder / ("source" + record["extension"])
        if not path.is_file() or path.resolve().parent != folder.resolve():
            return None
        return record | {"path": path}
    except (OSError, ValueError, KeyError):
        return None


def list_assets():
    folder = _root() / "assets"
    if not folder.exists():
        return []
    rows = []
    for item in folder.iterdir():
        record = _read_asset(item.name)
        if record:
            record.pop("path", None)
            rows.append(record)
    return sorted(rows, key=lambda item: item["created_at"], reverse=True)


def get_video_asset_path(asset_id):
    record = _read_asset(asset_id)
    if not record or record.get("kind") != "video":
        return None
    return record["path"], record["filename"]


def _run(command, timeout=3600):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VideoStudioError("Rendering took too long and was stopped. Try a shorter cut or smaller source files.", 504) from exc
    except OSError as exc:
        raise VideoStudioError(f"Could not start the video tool: {exc}", 503) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "The video tool returned an error.").strip()
        raise VideoStudioError(detail[-1800:], 422)
    return result.stdout.strip()


def _duration(asset, ffprobe):
    if asset["kind"] == "image":
        return None
    output = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(asset["path"])], timeout=90)
    try:
        seconds = float(output)
    except ValueError as exc:
        raise VideoStudioError(f"Could not read the duration of {asset['filename']}.", 422) from exc
    if seconds <= 0 or seconds > _MAX_TOTAL_SECONDS:
        raise VideoStudioError(f"{asset['filename']} must be longer than 0 and no longer than 60 minutes.", 422)
    return seconds


def render_video(title, asset_ids, image_seconds=5, audio_asset_id=None):
    status = tool_status()
    if not status["ffmpeg_available"] or not status["ffprobe_available"]:
        raise VideoStudioError("FFmpeg and FFprobe are required to render. Install FFmpeg, make sure both programs are on PATH, then restart Uvicorn.", 503)
    if len(set(asset_ids)) != len(asset_ids):
        raise VideoStudioError("Choose each timeline asset only once.")
    assets = [_read_asset(asset_id) for asset_id in asset_ids]
    if any(asset is None for asset in assets):
        raise VideoStudioError("A selected timeline asset is missing. Refresh the media list and try again.", 404)
    visuals = [asset for asset in assets if asset["kind"] in {"video", "image"}]
    if len(visuals) != len(assets) or not visuals:
        raise VideoStudioError("Add at least one video clip or picture to the timeline. Choose audio in the soundtrack selector.")
    if len(visuals) > 12:
        raise VideoStudioError("Add no more than 12 clips or pictures to one rough cut.", 413)
    audio = _read_asset(audio_asset_id) if audio_asset_id else None
    if audio_asset_id and (audio is None or audio["kind"] != "audio"):
        raise VideoStudioError("The selected soundtrack is missing or is not an audio file.", 404)
    ffmpeg, ffprobe = status["ffmpeg"], status["ffprobe"]
    durations = [_duration(asset, ffprobe) if asset["kind"] == "video" else float(image_seconds) for asset in visuals]
    total_seconds = sum(durations)
    if total_seconds > _MAX_TOTAL_SECONDS:
        raise VideoStudioError("A rough cut is limited to 60 minutes total. Shorten the clip selection.", 413)
    job_id = uuid.uuid4().hex
    root = _root()
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    title = re.sub(r"\s+", " ", str(title or "Untitled episode")).strip()[:120] or "Untitled episode"
    with tempfile.TemporaryDirectory(prefix=f"render-{job_id}-", dir=str(root)) as temp:
        work = Path(temp)
        segments = []
        vf = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p"
        for index, (asset, duration) in enumerate(zip(visuals, durations)):
            segment = work / f"clip-{index:02d}.mp4"
            if asset["kind"] == "image":
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "24", "-t", str(image_seconds), "-i", str(asset["path"]), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "24", str(segment)]
            else:
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(asset["path"]), "-map", "0:v:0", "-t", f"{duration:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "24", str(segment)]
            _run(command)
            segments.append(segment)
        concat = work / "timeline.txt"
        concat.write_text("".join(f"file '{str(path).replace(chr(92), '/')}'\n" for path in segments), encoding="utf-8")
        silent = work / "silent.mp4"
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(silent)])
        final_path = exports / f"{job_id}.mp4"
        if audio:
            _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(audio["path"]), "-i", str(silent), "-map", "1:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-metadata", f"title={title}", "-movflags", "+faststart", str(final_path)])
        else:
            shutil.copyfile(silent, final_path)
    job = {"id": job_id, "title": title, "filename": f"{title}.mp4", "asset_ids": asset_ids, "audio_asset_id": audio_asset_id, "duration_seconds": round(total_seconds, 2), "created_at": datetime.now(timezone.utc).isoformat(), "size": final_path.stat().st_size, "download_url": f"/v1/video/jobs/{job_id}/file"}
    (exports / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def list_jobs():
    folder = _root() / "exports"
    if not folder.exists():
        return []
    rows = []
    for path in folder.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if (folder / f"{job['id']}.mp4").is_file():
                rows.append(job)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda item: item.get("created_at", ""), reverse=True)


def get_job_path(job_id):
    if not _UUID.fullmatch(job_id or ""):
        return None
    path = _root() / "exports" / f"{job_id}.mp4"
    return path if path.is_file() else None


def blender_starter_script():
    return '''import bpy
import math

# Editable 3D title card starter. Run this in Blender's Scripting workspace.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100
scene.render.fps = 24
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = "//renders/title-card-"
scene.render.engine = "BLENDER_EEVEE_NEXT" if bpy.app.version >= (4, 2, 0) else "BLENDER_EEVEE"

bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -0.5))
bpy.context.object.name = "Stage floor"
bpy.ops.object.text_add(location=(0, 0, 0))
title = bpy.context.object
title.name = "EDIT - Show title"
title.data.body = "YOUR SHOW TITLE"
title.data.align_x = "CENTER"
title.data.align_y = "CENTER"
title.data.extrude = 0.035
title.data.bevel_depth = 0.008
title.data.size = 1.25
bpy.ops.object.camera_add(location=(0, -8, 2.2), rotation=(math.radians(78), 0, 0))
scene.camera = bpy.context.object
scene.camera.name = "Title card camera"
scene.camera.data.lens = 50
for location, energy, size in [((-3, -4, 5), 1400, 5), ((3, -2, 2), 900, 4)]:
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
scene.world.color = (0.025, 0.04, 0.08)
print("Starter scene ready. Edit the title, save the .blend file, and render your title card.")
'''
