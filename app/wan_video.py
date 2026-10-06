"""Local Wan 2.1 text-to-video engine powered by the ComfyUI Windows portable build."""

import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from app.video_studio import register_generated_video


_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME = _ROOT / "tools" / "runtime"
_PORTABLE = _RUNTIME / "ComfyUI_windows_portable"
_COMFY = _PORTABLE / "ComfyUI"
_PYTHON = _PORTABLE / "python_embeded" / "python.exe"
_BASE_MODELS = _ROOT / "base_models"
_MODEL = _BASE_MODELS / "wan2.1_t2v_1.3B_fp16.safetensors"
_TEXT_ENCODER = _BASE_MODELS / "Wan2.1" / "split_files" / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
_VAE = _BASE_MODELS / "Wan2.1" / "split_files" / "vae" / "wan_2.1_vae.safetensors"
_JOBS_DIR = _ROOT / "video_studio" / "generation_jobs"
_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
_JOBS = {}
_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _comfy_url():
    return _BASE_URL


def _portable_ready():
    return _PYTHON.is_file() and (_COMFY / "main.py").is_file()


def _models_ready():
    return all(path.is_file() and path.stat().st_size > 100_000_000 for path in (_MODEL, _TEXT_ENCODER, _VAE))


def _model_config_path():
    return _COMFY / "extra_model_paths.yaml"


def _configure_model_paths():
    config = _model_config_path()
    base = _BASE_MODELS.as_posix()
    text_encoder = (_BASE_MODELS / "Wan2.1" / "split_files" / "text_encoders").as_posix()
    vae = (_BASE_MODELS / "Wan2.1" / "split_files" / "vae").as_posix()
    config.write_text(
        "wan_project:\n"
        f"  base_path: '{base}'\n"
        "  diffusion_models: .\n"
        f"  text_encoders: '{text_encoder}'\n"
        f"  vae: '{vae}'\n",
        encoding="utf-8",
    )
    return config


def _server_ready():
    try:
        response = requests.get(f"{_comfy_url()}/system_stats", timeout=2)
        return response.ok
    except requests.RequestException:
        return False


def status():
    installed = _portable_ready()
    models = _models_ready()
    running = _server_ready()
    detail = "Wan model files are ready. Start the engine or generate a clip."
    if not installed:
        detail = "The local ComfyUI engine is not installed in this project yet."
    elif not models:
        detail = "Wan needs its text encoder and VAE files before it can generate video."
    elif running:
        detail = "Wan 2.1 is connected to the local video engine."
    return {
        "installed": installed,
        "running": running,
        "models_ready": models,
        "engine": "ComfyUI portable",
        "model": "Wan 2.1 T2V 1.3B",
        "url": _comfy_url(),
        "detail": detail,
        "files": {
            "diffusion_model": {"path": str(_MODEL), "ready": _MODEL.is_file()},
            "text_encoder": {"path": str(_TEXT_ENCODER), "ready": _TEXT_ENCODER.is_file()},
            "vae": {"path": str(_VAE), "ready": _VAE.is_file()},
        },
    }


def start_engine():
    if _server_ready():
        return {"status": "running", "detail": "The local video engine is already running."}
    if not _portable_ready():
        raise RuntimeError("The project-local ComfyUI engine has not been installed yet.")
    if not _models_ready():
        raise RuntimeError("Wan's diffusion model, text encoder, or VAE is missing or incomplete.")
    config = _configure_model_paths()
    command = [
        str(_PYTHON), "-s", str(_COMFY / "main.py"), "--windows-standalone-build",
        "--listen", "127.0.0.1", "--port", "8188", "--extra-model-paths-config", str(config),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        log_path = _RUNTIME / "comfyui.log"
        with log_path.open("ab") as log_file:
            subprocess.Popen(command, cwd=str(_PORTABLE), stdin=subprocess.DEVNULL, stdout=log_file,
                             stderr=subprocess.STDOUT, creationflags=creationflags, close_fds=True)
    except OSError as exc:
        raise RuntimeError(f"Could not start the local video engine: {exc}") from exc
    return {"status": "starting", "detail": "Starting ComfyUI locally; Wan models are loading into memory."}


def _write_job(job_id, **changes):
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        job = _JOBS.get(job_id, {"id": job_id, "created_at": _now()})
        job.update(changes, updated_at=_now())
        _JOBS[job_id] = job
        (_JOBS_DIR / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(job)


def get_job(job_id):
    with _LOCK:
        if job_id in _JOBS:
            return dict(_JOBS[job_id])
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        return None
    path = _JOBS_DIR / f"{job_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_jobs():
    if not _JOBS_DIR.exists():
        return []
    rows = []
    for path in _JOBS_DIR.glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)


def start_generation(title, prompt, negative_prompt="", width=832, height=480, frames=33):
    prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if len(prompt) < 8:
        raise ValueError("Describe the video you want in at least 8 characters.")
    if len(prompt) > 3000:
        raise ValueError("Keep the video prompt under 3,000 characters.")
    if (width, height) not in {(832, 480), (480, 832), (512, 512)}:
        raise ValueError("Choose one of the supported video sizes.")
    if frames not in {33, 49, 65}:
        raise ValueError("Choose 33, 49, or 65 frames for this local Wan preset.")
    if not _portable_ready():
        raise RuntimeError("The project-local ComfyUI engine is not installed yet.")
    if not _models_ready():
        raise RuntimeError("Wan's diffusion model, text encoder, or VAE is missing or incomplete.")
    job_id = uuid.uuid4().hex
    job = _write_job(job_id, status="queued", progress=None, title=str(title or "AI generated video")[:120],
                     prompt=prompt, negative_prompt=str(negative_prompt or "")[:1200], width=width,
                     height=height, frames=frames, message="Queued local Wan video generation.")
    threading.Thread(target=_run_generation, args=(job_id,), name=f"wan-video-{job_id[:8]}", daemon=True).start()
    return job


def _workflow(job):
    seed = int.from_bytes(os.urandom(8), "big") % (2**53)
    negative = job.get("negative_prompt") or "blurry, low quality, distorted anatomy, extra fingers, text, watermark, flicker"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": _MODEL.name, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": _TEXT_ENCODER.name, "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": _VAE.name}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": job["prompt"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": job["width"], "height": job["height"], "length": job["frames"], "batch_size": 1}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": seed, "control_after_generate": "randomize", "steps": 30, "cfg": 6, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 16}},
        "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": "video/AgentPlatform", "format": "auto", "codec": "auto"}},
    }


def _find_video_file(history):
    output = history.get("outputs") or {}
    for node in output.values():
        for key in ("videos", "gifs", "images"):
            for file_info in node.get(key, []) if isinstance(node, dict) else []:
                filename = str(file_info.get("filename") or "")
                if Path(filename).suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
                    return file_info
    return None


def _run_generation(job_id):
    job = get_job(job_id)
    try:
        start_engine()
        _write_job(job_id, status="starting", message="Starting the local video engine and loading Wan into memory.")
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and not _server_ready():
            time.sleep(2)
        if not _server_ready():
            raise RuntimeError("ComfyUI did not start in time. Check that port 8188 is free, then refresh Video Studio.")
        _write_job(job_id, status="submitting", message="Sending the prompt to Wan 2.1 on this computer.")
        response = requests.post(f"{_comfy_url()}/prompt", json={"prompt": _workflow(job), "client_id": job_id}, timeout=30)
        if not response.ok:
            raise RuntimeError(f"ComfyUI rejected the Wan workflow: {response.text[:1200]}")
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a video job ID.")
        _write_job(job_id, status="generating", prompt_id=prompt_id, message="Wan is generating your video locally. This can take several minutes.")
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            try:
                progress_result = requests.get(f"{_comfy_url()}/progress", timeout=5).json()
                maximum = float(progress_result.get("max") or 0)
                if maximum > 0:
                    percent = max(0, min(99, round(float(progress_result.get("value") or 0) * 100 / maximum)))
                    _write_job(job_id, progress=percent, message=f"Wan is generating your video locally · {percent}%.")
            except (requests.RequestException, ValueError, TypeError):
                pass
            result = requests.get(f"{_comfy_url()}/history/{prompt_id}", timeout=20)
            result.raise_for_status()
            history = result.json().get(prompt_id)
            if history:
                status_info = history.get("status", {})
                if status_info.get("status_str") == "error":
                    raise RuntimeError("Wan generation failed in ComfyUI. Check the engine console/log for details.")
                file_info = _find_video_file(history)
                if file_info:
                    view_params = {key: file_info[key] for key in ("filename", "subfolder", "type") if file_info.get(key) is not None}
                    media = requests.get(f"{_comfy_url()}/view", params=view_params, timeout=180)
                    media.raise_for_status()
                    if len(media.content) < 1024:
                        raise RuntimeError("The video engine returned an empty or incomplete video file.")
                    render_job = register_generated_video(job["title"], media.content, job["frames"] / 16, job["prompt"])
                    _write_job(job_id, status="complete", progress=100, message="Video generated and saved in Recent renders.", render_job=render_job)
                    return
                if status_info.get("status_str") == "success":
                    raise RuntimeError("ComfyUI finished Wan generation but did not return a video file. Check the engine log.")
                if status_info.get("status_str") == "error":
                    raise RuntimeError("Wan generation failed in ComfyUI. Check the engine console/log for details.")
            time.sleep(2)
        raise RuntimeError("Wan generation exceeded the one-hour limit and was stopped.")
    except Exception as exc:
        _write_job(job_id, status="error", error=str(exc)[:2000], message="Video generation failed.")
