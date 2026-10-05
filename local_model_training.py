"""Local supervised fine-tuning for private LoRA adapters."""
import json
import math
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MODELS = _ROOT / "trained_models"
_BASE_MODELS = _ROOT / "base_models"
_DATASETS = _MODELS / ".training-data"
_JOB_FILE = _MODELS / "training-jobs.json"
_SETTINGS_FILE = _MODELS / "training-settings.json"
_RESTORE_FILE = _MODELS / "base-model-restores.json"
_JOBS = {}
_ACTIVE = set()
_LOCK = threading.RLock()
_RESTORE_LOCK = threading.RLock()
_RESTORES = {}
_RECOMMENDED_BASE_MODELS = (
    "Qwen/Qwen2.5-0.5B-Instruct",
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen2.5-3B-Instruct",
    "Qwen/Qwen3-0.6B",
    "Qwen/Qwen3-1.7B",
    "HuggingFaceTB/SmolLM2-360M-Instruct",
    "HuggingFaceTB/SmolLM2-1.7B-Instruct",
)


def _utc():
    return datetime.now(timezone.utc).isoformat()


def get_training_device_mode():
    try:
        value = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8")).get("device_mode", "auto")
    except (OSError, ValueError, AttributeError):
        value = "auto"
    return value if value in {"auto", "cpu", "gpu"} else "auto"


def _hub_cache_roots():
    roots = []
    for variable in ("HF_HUB_CACHE", "TRANSFORMERS_CACHE"):
        value = os.environ.get(variable, "").strip()
        if value:
            root = Path(value).expanduser()
            roots.extend((root, root / "hub"))
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        roots.append(Path(hf_home).expanduser() / "hub")
    roots.extend((Path.home() / ".cache" / "huggingface" / "hub", _ROOT / "hf_cache" / "hub", _BASE_MODELS))
    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _project_base_model_path(repo_id):
    parts = str(repo_id or "").strip().split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return None
    return _BASE_MODELS / f"{parts[0]}--{parts[1]}"


def _load_restores():
    global _RESTORES
    with _RESTORE_LOCK:
        if _RESTORES:
            return
        try:
            saved = json.loads(_RESTORE_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, list):
                _RESTORES = {item["id"]: item for item in saved if isinstance(item, dict) and item.get("id")}
                changed = False
                for job in _RESTORES.values():
                    if job.get("status") in {"queued", "downloading"}:
                        job.update({"status": "error", "message": "The server restarted during this download. Choose Restore base model to continue; finished files are kept."})
                        changed = True
                if changed:
                    _save_restores_locked()
        except (OSError, ValueError):
            _RESTORES = {}


def _save_restores_locked():
    _MODELS.mkdir(parents=True, exist_ok=True)
    temporary = _RESTORE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(list(_RESTORES.values()), indent=2), encoding="utf-8")
    os.replace(temporary, _RESTORE_FILE)


def get_base_model_restore(restore_id):
    _load_restores()
    with _RESTORE_LOCK:
        job = _RESTORES.get(str(restore_id))
        return dict(job) if job else None


def start_base_model_restore(model_id):
    model_id = str(model_id or "").strip()
    parts = model_id.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Enter a valid Hugging Face model ID, such as Qwen/Qwen2.5-0.5B-Instruct.")
    project_copy = _project_base_model_path(model_id)
    if project_copy and project_copy.is_dir() and _snapshot_weights_are_usable(project_copy):
        return {"status": "complete", "model_id": model_id, "progress": 100, "message": "This base model is already copied into the project folder."}
    _load_restores()
    with _RESTORE_LOCK:
        active = next((job for job in _RESTORES.values() if job.get("model_id") == model_id and job.get("status") in {"queued", "downloading"}), None)
        if active:
            return dict(active)
        restore_id = uuid.uuid4().hex
        job = {"id": restore_id, "model_id": model_id, "status": "queued", "progress": 0, "message": "Preparing the local base-model download."}
        _RESTORES[restore_id] = job
        _save_restores_locked()
    threading.Thread(target=_restore_base_model_worker, args=(restore_id,), daemon=True, name=f"base-model-restore-{restore_id[:8]}").start()
    return dict(job)


def _restore_base_model_worker(restore_id):
    _load_restores()
    with _RESTORE_LOCK:
        job = _RESTORES.get(restore_id)
        if not job:
            return
        job["status"] = "downloading"
        job["message"] = "Preparing the project-local model copy. First downloads can be large; keep Uvicorn running."
        _save_restores_locked()
    try:
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm
        target = _project_base_model_path(job["model_id"])
        if not target:
            raise ValueError("That Hugging Face model ID cannot be used as a local folder name.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and _snapshot_weights_are_usable(target):
            result = {"status": "complete", "progress": 100, "message": "Base model is already copied into this project and ready for local use.", "local_path": str(target.resolve())}
            with _RESTORE_LOCK:
                current = _RESTORES.get(restore_id)
                if current:
                    current.update(result)
                    _save_restores_locked()
            return

        # Reuse a valid copy in any existing Hugging Face cache before using the
        # network. Copying dereferences Hub cache links into stable project files.
        existing = _cached_repo_snapshots(job["model_id"])
        stage = _BASE_MODELS / ".staging" / f"{target.name}-{restore_id[:8]}"
        if existing:
            with _RESTORE_LOCK:
                current = _RESTORES.get(restore_id)
                if current:
                    current["message"] = "Copying the existing cached model into this project; no download is needed."
                    current["progress"] = 1
            if stage.exists():
                shutil.rmtree(stage)
            shutil.copytree(existing[0], stage)
            if not _snapshot_weights_are_usable(stage):
                raise RuntimeError("The cached model could not be copied as complete local files. Its cache may contain missing links.")
        else:
            # Keep a stable staging folder so Hugging Face can resume partial
            # transfers if Uvicorn or the network stops.
            stage = _BASE_MODELS / ".staging" / target.name
            stage.mkdir(parents=True, exist_ok=True)

        class RestoreProgress(tqdm):
            def update(self, n=1):
                changed = super().update(n)
                bars = getattr(self, "_restore_bars", None)
                if bars is not None:
                    bars[id(self)] = (max(0, self.n), self.total)
                    totals = [total for _, total in bars.values() if total and total > 0]
                    if totals:
                        downloaded = sum(value for value, _ in bars.values())
                        total_bytes = sum(total for _, total in bars.values() if total and total > 0)
                        percent = min(99, int(downloaded * 100 / total_bytes)) if total_bytes else 0
                        with _RESTORE_LOCK:
                            current = _RESTORES.get(restore_id)
                            if current:
                                old_percent = current.get("progress", 0)
                                if percent > old_percent:
                                    current["progress"] = percent
                                    current["message"] = f"Downloading base model into this project · {percent}%"
                                    if percent == 99 or percent - old_percent >= 5:
                                        _save_restores_locked()
                return changed

            def __init__(self, *args, **kwargs):
                kwargs.setdefault("disable", True)
                super().__init__(*args, **kwargs)
                self._restore_bars = _restore_progress_bars

        _restore_progress_bars = {}
        if not existing:
            snapshot_download(
                repo_id=job["model_id"],
                local_dir=str(stage),
                allow_patterns=["*.safetensors", "*.json", "*.txt", "*.model", "*.jinja", "tokenizer*"],
                tqdm_class=RestoreProgress,
            )
        if not _snapshot_weights_are_usable(stage):
            raise RuntimeError("The transfer ended, but required model files are missing or incomplete in the project staging folder. Retry to resume it.")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, target)
        result = {"status": "complete", "progress": 100, "message": "Base model is copied into this project and ready for local training and replies.", "local_path": str(target.resolve())}
    except Exception as exc:
        result = {"status": "error", "progress": 0, "message": f"Could not restore this model: {str(exc)[:600]}"}
    with _RESTORE_LOCK:
        current = _RESTORES.get(restore_id)
        if current:
            current.update(result)
            _save_restores_locked()


def _cached_repo_snapshots(repo_id):
    parts = str(repo_id or "").strip().split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return []
    folder_name = f"models--{parts[0]}--{parts[1]}"
    snapshots = []
    project_copy = _project_base_model_path(repo_id)
    if project_copy and project_copy.is_dir() and _snapshot_weights_are_usable(project_copy):
        snapshots.append(project_copy)
    for root in _hub_cache_roots():
        try:
            for snapshot in (root / folder_name / "snapshots").iterdir():
                if not snapshot.is_dir() or not (snapshot / "config.json").is_file():
                    continue
                if _snapshot_weights_are_usable(snapshot):
                    snapshots.append(snapshot)
        except OSError:
            continue
    return sorted(snapshots, key=lambda item: item.stat().st_mtime, reverse=True)


def _snapshot_weights_are_usable(snapshot):
    index_path = snapshot / "model.safetensors.index.json"
    if index_path.is_file():
        try:
            weight_map = json.loads(index_path.read_text(encoding="utf-8")).get("weight_map", {})
            shards = {snapshot / name for name in weight_map.values()}
        except (OSError, ValueError, AttributeError):
            return False
        if not shards:
            return False
    else:
        shards = set(snapshot.glob("*.safetensors")) | set(snapshot.glob("pytorch_model*.bin"))
    if not shards:
        return False
    for shard in shards:
        try:
            if not shard.is_file() or shard.stat().st_size <= 0:
                return False
            if shard.suffix == ".safetensors":
                with shard.open("rb") as stream:
                    prefix = stream.read(8)
                    if len(prefix) != 8:
                        return False
                    header_size = int.from_bytes(prefix, "little")
                    if header_size < 2 or header_size > 100 * 1024 * 1024 or header_size + 8 > shard.stat().st_size:
                        return False
                    json.loads(stream.read(header_size).decode("utf-8").rstrip(" "))
        except (OSError, UnicodeDecodeError, ValueError):
            return False
    return True


def list_pretrained_base_models():
    """List cached chat models and a short set of public Hub instruct models."""
    cached = {}
    for root in _hub_cache_roots():
        try:
            repos = root.glob("models--*--*")
            for repo in repos:
                encoded = repo.name[len("models--"):]
                namespace, separator, name = encoded.partition("--")
                if not separator or not namespace or not name:
                    continue
                repo_id = f"{namespace}/{name}"
                snapshots = _cached_repo_snapshots(repo_id)
                if not snapshots:
                    continue
                try:
                    tokenizer_config = json.loads((snapshots[0] / "tokenizer_config.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    tokenizer_config = {}
                chat_model = bool(tokenizer_config.get("chat_template")) or any(
                    marker in repo_id.casefold() for marker in ("instruct", "-chat", "/chat", "-it")
                )
                if chat_model:
                    cached[repo_id] = {"id": repo_id, "cached": True}
        except OSError:
            continue
    try:
        for folder in _BASE_MODELS.iterdir():
            if not folder.is_dir() or folder.name.startswith((".", "models--")) or not _snapshot_weights_are_usable(folder):
                continue
            namespace, separator, name = folder.name.partition("--")
            if separator and namespace and name:
                repo_id = f"{namespace}/{name}"
                cached[repo_id] = {"id": repo_id, "cached": True}
    except OSError:
        pass
    models = list(cached.values())
    for repo_id in _RECOMMENDED_BASE_MODELS:
        if repo_id not in cached:
            models.append({"id": repo_id, "cached": False})
    return sorted(models, key=lambda item: (not item["cached"], item["id"].casefold()))


def _find_cached_base_model(repo_id):
    project_copy = _project_base_model_path(repo_id)
    if project_copy and project_copy.is_dir() and _snapshot_weights_are_usable(project_copy):
        return str(project_copy.resolve())
    snapshots = _cached_repo_snapshots(repo_id)
    return str(snapshots[0].resolve()) if snapshots else ""


def set_training_device_mode(device_mode):
    if device_mode not in {"auto", "cpu", "gpu"}:
        raise ValueError("Choose Automatic, CPU, or NVIDIA GPU mode.")
    _MODELS.mkdir(parents=True, exist_ok=True)
    temporary = _SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"device_mode": device_mode}, indent=2), encoding="utf-8")
    os.replace(temporary, _SETTINGS_FILE)
    return get_training_device_mode()


def _save_jobs_locked():
    _MODELS.mkdir(parents=True, exist_ok=True)
    temporary = _JOB_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(list(_JOBS.values()), indent=2), encoding="utf-8")
    os.replace(temporary, _JOB_FILE)


def _load_jobs():
    if not _JOB_FILE.is_file():
        return
    try:
        rows = json.loads(_JOB_FILE.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return
        for job in rows:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "interrupted"
                job["progress"] = "The app restarted. Your saved training data is ready to resume."
                job["updated_at"] = _utc()
            _JOBS[job["id"]] = job
        with _LOCK:
            _save_jobs_locked()
    except (OSError, ValueError, TypeError):
        # Keep a malformed state file for recovery; the app can still run and
        # newly created jobs will be saved after the user starts another run.
        return


_load_jobs()


def hardware_status():
    device_mode = get_training_device_mode()
    status = {"ready": False, "dependencies_ready": False, "cuda_available": False, "gpu_name": "", "device_mode": device_mode, "active_device": "cpu", "python_executable": sys.executable, "torch_version": "", "torch_cuda_version": None, "message": "Install the optional local training dependencies to begin."}
    try:
        import importlib.util
        names = ("torch", "transformers", "datasets", "peft", "trl", "accelerate")
        missing = [name for name in names if importlib.util.find_spec(name) is None]
        status["dependencies_ready"] = not missing
        if missing:
            status["message"] = "Missing training packages: " + ", ".join(missing) + ". Install requirements-training-gpu.txt for NVIDIA GPU use, or requirements-training-cpu.txt for CPU-only use, in this project's .venv."
            return status
        import torch
        status["torch_version"] = torch.__version__
        status["torch_cuda_version"] = torch.version.cuda
        status["cuda_available"] = torch.cuda.is_available()
        status["gpu_name"] = torch.cuda.get_device_name(0) if status["cuda_available"] else ""
        use_gpu = device_mode != "cpu" and status["cuda_available"]
        status["active_device"] = "gpu" if use_gpu else "cpu"
        status["ready"] = not (device_mode == "gpu" and not status["cuda_available"])
        if device_mode == "gpu" and not status["cuda_available"]:
            status["message"] = "GPU mode is selected, but this project environment cannot access CUDA. Install the NVIDIA GPU packages into this project's .venv, or switch to CPU."
        elif use_gpu:
            status["message"] = "Training will use " + status["gpu_name"] + "."
        elif device_mode == "cpu":
            status["message"] = "CPU mode is selected. Training will stay on the CPU even though GPU support is available."
        elif torch.version.cuda is None:
            status["message"] = "Automatic mode selected, but this environment has CPU-only PyTorch. Training will use the CPU; install NVIDIA GPU packages in this project's .venv to use CUDA."
        else:
            status["message"] = "Training packages are ready. Automatic mode will use the CPU because CUDA is unavailable."
    except Exception as exc:
        status["message"] = "Could not inspect the local training environment: " + str(exc)[:300]
    return status


def _parse_dataset(text):
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Training file line {line_number} is not valid JSON: {exc.msg}.") from exc
        messages = row.get("messages") if isinstance(row, dict) else None
        if not isinstance(messages, list):
            raise ValueError(f"Line {line_number} must contain a messages array with user and assistant examples.")
        valid = [m for m in messages if isinstance(m, dict) and m.get("role") in {"system", "user", "assistant"} and isinstance(m.get("content"), str) and m["content"].strip()]
        if len(valid) < 2 or not any(m["role"] == "user" for m in valid) or not any(m["role"] == "assistant" for m in valid):
            raise ValueError(f"Line {line_number} needs at least one user message and one assistant answer.")
        rows.append({"messages": valid})
        if len(rows) > 50000:
            raise ValueError("Training data is limited to 50,000 conversation examples per run.")
    if len(rows) < 2:
        raise ValueError("Add at least two JSONL conversation examples before training.")
    return rows


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-_")[:48]
    return value or "custom-agent-model"


def list_models():
    _MODELS.mkdir(parents=True, exist_ok=True)
    result = []
    for config in _MODELS.glob("*/adapter_config.json"):
        folder = config.parent
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            manifest_path = folder / "model-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            base_model = data.get("base_model_name_or_path", "")
            project_copy = _project_base_model_path(base_model)
            project_ready = bool(project_copy and project_copy.is_dir() and _snapshot_weights_are_usable(project_copy))
            result.append({"id": folder.name, "name": manifest.get("name", folder.name), "model_id": str(folder.resolve()), "base_model": base_model, "base_model_cached": bool(_find_cached_base_model(base_model)), "base_model_project_copy": project_ready, "created_at": manifest.get("created_at", ""), "size_bytes": sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())})
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["name"].lower())


def create_model_backup(model_id):
    if not model_id or Path(model_id).name != model_id:
        raise ValueError("Choose a saved model to download.")
    folder = (_MODELS / model_id).resolve()
    if not folder.is_relative_to(_MODELS.resolve()) or not (folder / "adapter_config.json").is_file():
        raise FileNotFoundError("That saved local model was not found in this project.")
    exports = _MODELS / ".exports"
    exports.mkdir(parents=True, exist_ok=True)
    archive = exports / f"{folder.name}.zip"
    temporary = exports / f".{folder.name}-{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4) as output:
            for item in folder.rglob("*"):
                if item.is_file():
                    output.write(item, Path(folder.name) / item.relative_to(folder))
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def get_jobs():
    with _LOCK:
        return [
            {**job, "resume_available": job.get("status") == "interrupted"}
            for job in sorted(_JOBS.values(), key=lambda job: job["created_at"], reverse=True)
        ]


def get_job(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _remove_training_path(path):
    """Remove a failed training artifact despite Windows read-only files."""
    target = Path(path)

    def clear_readonly_and_retry(function, failed_path, exc_info):
        error = exc_info[1]
        try:
            os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD)
            function(failed_path)
        except OSError:
            raise error

    last_error = None
    for attempt in range(3):
        try:
            if target.is_dir():
                shutil.rmtree(target, onerror=clear_readonly_and_retry)
            elif target.exists():
                try:
                    target.unlink()
                except PermissionError:
                    os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
                    target.unlink()
            return
        except OSError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    raise ValueError(
        f"Windows could not remove the failed training files at '{target}'. "
        "Close any app using that folder, wait for OneDrive to finish syncing, and try Remove failed job again."
    ) from last_error


def delete_failed_job(job_id):
    """Remove a failed job and its incomplete data, preserving completed adapters."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise FileNotFoundError("Training job was not found.")
        if job.get("status") != "error" or job_id in _ACTIVE:
            raise ValueError("Only a stopped job with an error can be removed here.")

        output_folder = job.get("output_folder", "")
        output = (_MODELS / output_folder).resolve() if output_folder and Path(output_folder).name == output_folder else None
        if output and output.parent != _MODELS.resolve():
            raise ValueError("The failed job's output folder is invalid, so it was not removed.")
        working = Path(str(output) + "-working").resolve() if output else None
        if working and working.parent != _MODELS.resolve():
            raise ValueError("The failed job's checkpoint folder is invalid, so it was not removed.")

        dataset = (_ROOT / job.get("dataset_file", "")).resolve()
        if job.get("dataset_file") and not dataset.is_relative_to(_DATASETS.resolve()):
            raise ValueError("The failed job's training-data location is invalid, so it was not removed.")

        # Keep a complete adapter if training reached the save step before an
        # unrelated finalization error; it remains listed as a saved model.
        if output and output.is_dir() and not (output / "adapter_config.json").is_file():
            _remove_training_path(output)
        if working and working.is_dir():
            _remove_training_path(working)
        if job.get("dataset_file") and dataset.is_file():
            _remove_training_path(dataset)
        _JOBS.pop(job_id, None)
        _save_jobs_locked()
    return {"status": "removed", "job_id": job_id}


def start_training(name, base_model, dataset_text, epochs=3, agent_id=""):
    if len(dataset_text.encode("utf-8")) > 12 * 1024 * 1024:
        raise ValueError("Training data must be 12 MB or smaller.")
    rows = _parse_dataset(dataset_text)
    status = hardware_status()
    if not status["dependencies_ready"]:
        raise RuntimeError(status["message"])
    device_mode = status["device_mode"]
    if device_mode == "gpu" and not status["cuda_available"]:
        raise RuntimeError(status["message"])
    from app.platform_store import get_agent, reserve_agent_tokens
    agent = get_agent(agent_id)
    if not agent or not agent.get("enabled"):
        raise ValueError("Choose an active agent to own this model and its training allowance.")
    estimated_tokens = max(1, math.ceil(sum(len(message["content"]) for row in rows for message in row["messages"]) / 4 * epochs))
    reserved_tokens = math.ceil(estimated_tokens * 1.2)
    if not reserve_agent_tokens(agent_id, reserved_tokens):
        raise ValueError(f"{agent['name']} does not have enough estimated tokens for this training run. Top up its local allowance first.")
    job_id = uuid.uuid4().hex
    output = _MODELS / f"{_safe_name(name)}-{job_id[:8]}"
    dataset = _DATASETS / f"{job_id}.jsonl"
    try:
        _DATASETS.mkdir(parents=True, exist_ok=True)
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        job = {"id": job_id, "name": _safe_name(name), "base_model": base_model.strip(), "model_id": "", "agent_id": agent_id, "agent_name": agent["name"], "reserved_tokens": reserved_tokens, "estimated_tokens": estimated_tokens, "status": "queued", "progress": "Waiting for the local trainer…", "examples": len(rows), "epochs": epochs, "device_mode": device_mode, "created_at": _utc(), "updated_at": _utc(), "error": "", "dataset_file": str(dataset.relative_to(_ROOT)), "output_folder": output.name}
        with _LOCK:
            _JOBS[job_id] = job
            _ACTIVE.add(job_id)
            _save_jobs_locked()
        threading.Thread(target=_train, args=(job_id, base_model.strip(), rows, output, epochs, device_mode), daemon=True).start()
    except Exception:
        with _LOCK:
            _ACTIVE.discard(job_id)
            _JOBS.pop(job_id, None)
            _save_jobs_locked()
        from app.platform_store import settle_agent_tokens
        settle_agent_tokens(agent_id, reserved_tokens, 0)
        raise
    return dict(job)


def _update(job_id, **values):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(values, updated_at=_utc())
            _save_jobs_locked()


def resume_training(job_id):
    from app.platform_store import get_agent

    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise ValueError("Training job was not found in the saved project history.")
        if job.get("status") not in {"interrupted", "queued"} or job_id in _ACTIVE:
            raise ValueError("This training job is already running or cannot be resumed.")
        dataset = (_ROOT / job.get("dataset_file", "")).resolve()
        if not dataset.is_relative_to(_DATASETS.resolve()) or not dataset.is_file():
            raise ValueError("The saved training examples are missing, so this job cannot resume.")
        agent = get_agent(job.get("agent_id"))
        if not agent or not agent.get("enabled"):
            raise ValueError("Enable the agent assigned to this job before resuming its training.")
        output_folder = job.get("output_folder", "")
        if not output_folder or Path(output_folder).name != output_folder:
            raise ValueError("The saved model output folder is invalid.")
        rows = _parse_dataset(dataset.read_text(encoding="utf-8"))
        output = _MODELS / output_folder
        working_dir = Path(str(output) + "-working")
        device_mode = job.get("device_mode", get_training_device_mode())
        if device_mode == "gpu" and not hardware_status()["cuda_available"]:
            raise ValueError("This saved job is set to GPU mode, but CUDA is not available in this project environment. Install the NVIDIA training packages or change the device to CPU before resuming.")
        has_checkpoint = any((item / "trainer_state.json").is_file() for item in working_dir.glob("checkpoint-*")) if working_dir.is_dir() else False
        _JOBS[job_id]["resume_count"] = int(_JOBS[job_id].get("resume_count", 0)) + 1
        _JOBS[job_id].update(status="queued", progress="Preparing to resume from the saved checkpoint…" if has_checkpoint else "No checkpoint was saved yet; the run will restart from the beginning using your saved examples.", error="", updated_at=_utc())
        _ACTIVE.add(job_id)
        _save_jobs_locked()
        job = dict(_JOBS[job_id])
    threading.Thread(target=_train, args=(job_id, job["base_model"], rows, output, int(job["epochs"]), device_mode), daemon=True).start()
    return job


def resume_interrupted_jobs():
    resumed = []
    for job in get_jobs():
        if job.get("status") != "interrupted":
            continue
        try:
            resumed.append(resume_training(job["id"]))
        except (ValueError, OSError) as exc:
            _update(job["id"], progress="Training is paused and can be resumed from the page when the saved files are available.", error=str(exc)[:500])
    return resumed


def _train(job_id, base_model, rows, output, epochs, device_mode="auto"):
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
        from trl import SFTConfig, SFTTrainer
        _update(job_id, status="running", progress="Loading the pretrained base model locally…")
        if not Path(base_model).expanduser().is_dir() and len(base_model.split("/")) == 2:
            restore = start_base_model_restore(base_model)
            restore_id = restore.get("id")
            while restore_id and restore.get("status") in {"queued", "downloading"}:
                _update(job_id, progress=restore.get("message") or "Copying the base model into this project…")
                time.sleep(2)
                restore = get_base_model_restore(restore_id) or restore
            if restore.get("status") == "error":
                raise RuntimeError(restore.get("message", "Could not prepare the base model in this project."))
        cached_base = _find_cached_base_model(base_model)
        model_source = cached_base or base_model
        local_only = bool(cached_base) or Path(model_source).is_dir()
        tokenizer = AutoTokenizer.from_pretrained(model_source, use_fast=True, local_files_only=local_only)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        cuda = device_mode != "cpu" and torch.cuda.is_available()
        if device_mode == "gpu" and not cuda:
            raise RuntimeError("GPU mode was selected, but CUDA is unavailable in the training environment.")
        # Prefer BF16 on GPUs that support it. It avoids GradScaler's FP16
        # unscale path, which can fail when PEFT exposes BF16 gradients (as it
        # does on some CUDA/PyTorch combinations). Fall back to FP16 elsewhere.
        bf16_supported = bool(cuda and torch.cuda.is_bf16_supported())
        train_dtype = torch.bfloat16 if bf16_supported else (torch.float16 if cuda else torch.float32)
        model_kwargs = {"device_map": "auto" if cuda else None, "dtype": train_dtype}
        quantized = False
        if cuda:
            try:
                from transformers import BitsAndBytesConfig
                import importlib.util
                if importlib.util.find_spec("bitsandbytes"):
                    model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=train_dtype, bnb_4bit_use_double_quant=True)
                    quantized = True
            except Exception:
                quantized = False
        model = AutoModelForCausalLM.from_pretrained(model_source, local_files_only=local_only, **model_kwargs)
        _update(job_id, progress="Fine-tuning the adapter on your conversation examples…")
        dataset = Dataset.from_list(rows)
        working_dir = Path(str(output) + "-working")
        training_args = SFTConfig(output_dir=str(working_dir), num_train_epochs=epochs, per_device_train_batch_size=1, gradient_accumulation_steps=4, learning_rate=2e-4, logging_steps=1, save_strategy="epoch", save_total_limit=2, report_to="none", max_length=1024, packing=False, fp16=bool(cuda and not bf16_supported), bf16=bf16_supported, use_cpu=not cuda, gradient_checkpointing=bool(cuda), optim="paged_adamw_8bit" if quantized else "adamw_torch")
        adapter = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM", target_modules="all-linear")

        class SaveJobProgress(TrainerCallback):
            def __init__(self):
                self.last_step = -1

            def on_log(self, args, state, control, **kwargs):
                step = int(state.global_step)
                if step == 1 or step % 5 == 0 or step == int(state.max_steps):
                    epoch = f" · pass {state.epoch:.1f}/{epochs}" if state.epoch is not None else ""
                    _update(job_id, progress=f"Training locally · step {step}/{state.max_steps}{epoch}")

            def on_save(self, args, state, control, **kwargs):
                step = int(state.global_step)
                _update(job_id, progress=f"Saved a recovery checkpoint at step {step}.")

        trainer = SFTTrainer(model=model, args=training_args, train_dataset=dataset, processing_class=tokenizer, peft_config=adapter, callbacks=[SaveJobProgress()])
        checkpoints = []
        if working_dir.is_dir():
            for checkpoint in working_dir.glob("checkpoint-*"):
                try:
                    step = int(checkpoint.name.rsplit("-", 1)[1])
                except (IndexError, ValueError):
                    continue
                if (checkpoint / "trainer_state.json").is_file():
                    checkpoints.append((step, checkpoint))
        latest = max(checkpoints, default=(0, None), key=lambda item: item[0])[1]
        if latest:
            _update(job_id, progress=f"Resuming from saved checkpoint at step {latest.name.rsplit('-', 1)[-1]}…")
            trainer.train(resume_from_checkpoint=str(latest))
        else:
            resume_count = int(_JOBS.get(job_id, {}).get("resume_count", 0))
            message = "No saved checkpoint was available, so training restarted from the beginning using your saved examples." if resume_count else "Starting training. Progress will be saved after each training pass…"
            _update(job_id, progress=message)
            trainer.train()
        output.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(output))
        tokenizer.save_pretrained(str(output))
        adapter_config_path = output / "adapter_config.json"
        if adapter_config_path.is_file():
            adapter_config = json.loads(adapter_config_path.read_text(encoding="utf-8"))
            adapter_config["base_model_name_or_path"] = base_model
            adapter_config_path.write_text(json.dumps(adapter_config, indent=2), encoding="utf-8")
        (output / "model-manifest.json").write_text(json.dumps({"name": _JOBS[job_id]["name"], "base_model": base_model, "created_at": _utc(), "examples": len(rows), "epochs": epochs, "method": "LoRA"}, indent=2), encoding="utf-8")
        from app.platform_store import update_agent
        agent_id = _JOBS[job_id].get("agent_id")
        assigned = update_agent(agent_id, {"provider": "local-finetune", "model_id": str(output.resolve())})
        if not assigned:
            raise RuntimeError("Training finished, but the selected agent could not be updated. The adapter is saved locally.")
        _update(job_id, status="complete", progress="Your local model adapter is ready and assigned to the selected agent.", model_id=str(output.resolve()))
    except Exception as exc:
        _update(job_id, status="error", progress="Training stopped.", error=str(exc)[:1200])
    finally:
        try:
            from app.platform_store import settle_agent_tokens
            job = _JOBS.get(job_id, {})
            reserved = job.get("reserved_tokens", 0)
            actual = job.get("estimated_tokens", reserved) if job.get("status") == "complete" else 0
            if reserved:
                settle_agent_tokens(job.get("agent_id"), reserved, actual)
                _update(job_id, reserved_tokens=reserved, training_tokens_used=actual)
        except Exception as exc:
            _update(job_id, error=(str(exc)[:300] or "Could not settle local training usage."))
        finally:
            with _LOCK:
                _ACTIVE.discard(job_id)
                _save_jobs_locked()
