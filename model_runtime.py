"""Text-generation adapters for local Ollama and Hugging Face models."""

import json
import os
import re
import requests
from functools import lru_cache
from pathlib import Path


def generate(provider, model_id, system_prompt, prompt, timeout=180, images=None, api_key=None):
    provider = (provider or "").strip().lower()
    images = images or []
    if provider == "local-finetune":
        return _local_finetuned_generate(model_id, system_prompt, prompt, timeout)
    if provider == "ollama":
        return _ollama_generate(model_id, system_prompt, prompt, timeout, images)
    if provider == "huggingface":
        return _huggingface_generate(model_id, system_prompt, prompt, timeout, images, api_key)
    if provider == "openai":
        return _openai_compatible_generate(
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model_id, system_prompt, prompt, timeout, images, api_key or "", "OpenAI-compatible provider",
        )
    if provider == "docker-model-runner":
        return _openai_compatible_generate(
            os.getenv("DOCKER_MODEL_RUNNER_BASE_URL", "http://127.0.0.1:12434/engines/v1"),
            model_id, system_prompt, prompt, timeout, images,
        )
    if provider == "docker-agent":
        return _openai_compatible_generate(
            os.getenv("DOCKER_AGENT_BASE_URL", "http://127.0.0.1:8083/v1"),
            model_id, system_prompt, prompt, timeout, images,
            api_key or os.getenv("DOCKER_AGENT_API_KEY", "").strip(),
        )
    raise ValueError(f"Unsupported model provider '{provider}'. Choose Ollama, Hugging Face, Docker Model Runner, or Docker Agent.")


def _ollama_generate(model_id, system_prompt, prompt, timeout, images=None):
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        response = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model_id,
                "system": system_prompt or "You are a helpful AI agent on a specialist team.",
                "prompt": prompt,
                "stream": False,
                "images": [item["base64"] for item in (images or [])],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.ConnectionError as exc:
        raise RuntimeError(f"Ollama is not reachable at {base_url}. Start Ollama and pull '{model_id}'.") from exc
    except requests.Timeout as exc:
        raise RuntimeError(f"Ollama did not finish '{model_id}' within {timeout} seconds.") from exc
    except requests.HTTPError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise RuntimeError(f"Ollama could not run '{model_id}': {detail}") from exc
    answer = data.get("response", "").strip()
    if not answer:
        raise RuntimeError(f"Ollama returned an empty response for '{model_id}'.")
    return answer


def _image_content(prompt, images):
    if not images:
        return prompt
    parts = [{"type": "text", "text": prompt}]
    for item in images:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{item['media_type']};base64,{item['base64']}"},
        })
    return parts


def _openai_compatible_generate(base_url, model_id, system_prompt, prompt, timeout, images=None, api_key="", label="Local Docker model"):
    endpoint = base_url.rstrip("/") + "/chat/completions"
    runtime_label = "Docker Model Runner" if "12434" in base_url else "Docker Agent" if "8083" in base_url else label
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system_prompt or "You are a helpful AI agent on a specialist team."},
                    {"role": "user", "content": _image_content(prompt, images or [])},
                ],
                "max_tokens": 1600,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
    except requests.ConnectionError as exc:
        raise RuntimeError(f"{runtime_label} is not reachable at {base_url}. Check its API server and model settings.") from exc
    except requests.Timeout as exc:
        raise RuntimeError(f"{runtime_label} model '{model_id}' did not finish within {timeout} seconds.") from exc
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        detail = response.text[:500] if "response" in locals() else str(exc)
        raise RuntimeError(f"{runtime_label} model '{model_id}' could not respond: {detail}") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError(f"{runtime_label} model '{model_id}' returned an empty response.")
    return answer.strip()


def _huggingface_generate(model_id, system_prompt, prompt, timeout, images=None, api_key=None):
    model_path = Path(model_id).expanduser()
    has_transformers_weights = model_path.is_dir() and any(
        next(model_path.glob(pattern), None) is not None
        for pattern in ("*.safetensors", "pytorch_model*.bin", "model*.bin")
    )
    if model_path.is_dir() and (model_path / "config.json").is_file() and has_transformers_weights:
        return _local_huggingface_generate(str(model_path.resolve()), system_prompt, prompt, timeout, images)

    model_id = _huggingface_repo_id(model_id)
    token = (api_key or os.getenv("HF_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("Set HF_TOKEN in .env to use Hugging Face models.")
    try:
        from huggingface_hub import InferenceClient
    except ImportError as exc:
        raise RuntimeError("Hugging Face support is not installed. Install requirements.txt and restart Uvicorn.") from exc

    try:
        client = InferenceClient(model=model_id, token=token, timeout=timeout)
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt or "You are a helpful AI agent on a specialist team."},
                {"role": "user", "content": _image_content(prompt, images or [])},
            ],
            max_tokens=1200,
        )
        answer = response.choices[0].message.content
    except Exception as exc:
        # Hugging Face raises provider-specific HTTP and inference errors. Keep the
        # public response actionable without returning request headers or secrets.
        raise RuntimeError(f"Hugging Face could not run '{model_id}': {exc}") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError(f"Hugging Face returned an empty response for '{model_id}'.")
    return answer.strip()


def _huggingface_repo_id(model_id):
    """Accept either a Hub ID or a path copied from the Hugging Face Hub cache."""
    value = str(model_id or "").strip()
    # Hub cache folder names encode namespace/repository as models--owner--name.
    # Decode that form, including paths ending in snapshots/<revision>.
    for component in re.split(r"[\\/]+", value):
        if component.startswith("models--"):
            encoded = component[len("models--"):]
            namespace, separator, name = encoded.partition("--")
            if separator and namespace and name:
                return f"{namespace}/{name}"
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise RuntimeError(
            "This local model folder is not a Transformers model folder or a recognized Hugging Face cache folder. "
            "For a Hub model, enter its short name like organization/model-name. "
            "For a local Transformers model, select the folder that contains config.json."
        )
    return value


@lru_cache(maxsize=1)
def _load_local_huggingface_model(model_path):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Local Hugging Face model support needs PyTorch and Transformers installed in this project's environment."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, local_files_only=True)
    if torch.cuda.is_available():
        model = AutoModelForCausalLM.from_pretrained(
            model_path, device_map="auto", dtype="auto", local_files_only=True
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path, dtype=torch.float32, local_files_only=True
        )
    model.eval()
    return tokenizer, model


def _local_huggingface_generate(model_path, system_prompt, prompt, timeout, images=None):
    if images:
        raise RuntimeError(
            "This local Transformers model accepts text only in the current app. Use a vision-enabled Ollama model for image attachments."
        )
    try:
        import torch
        tokenizer, model = _load_local_huggingface_model(model_path)
        messages = [
            {"role": "system", "content": system_prompt or "You are a helpful AI agent on a specialist team."},
            {"role": "user", "content": prompt},
        ]
        if not getattr(tokenizer, "chat_template", None):
            raise RuntimeError("This local model has no chat template. Choose an instruct or chat model folder.")
        encoded = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        device = next(model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with _LOCAL_MODEL_LOCK, torch.inference_mode():
            output = model.generate(
                **encoded, max_new_tokens=1200, do_sample=True, temperature=0.7,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            )
        answer = tokenizer.decode(output[0][encoded["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"The local Hugging Face model could not respond: {str(exc)[:500]}") from exc
    if not answer:
        raise RuntimeError("The local Hugging Face model returned an empty response.")
    return answer


import threading as _threading
from functools import lru_cache as _lru_cache
_LOCAL_MODEL_LOCK = _threading.Lock()


def _load_local_adapter(adapter_path):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    config_path = os.path.join(adapter_path, "adapter_config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            base_model = json.load(config_file).get("base_model_name_or_path", "")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read the saved adapter configuration: {exc}") from exc

    base_path = _find_local_base_model(base_model)
    if not base_path:
        raise RuntimeError(
            f"The trained adapter is saved, but its base model '{base_model}' is not available in the local Hugging Face caches. "
            "Restore/download that base model once, then retry. Your trained adapter has not been deleted."
        )

    tokenizer = AutoTokenizer.from_pretrained(adapter_path, use_fast=True, local_files_only=True)
    model_kwargs = {"dtype": "auto", "local_files_only": True}
    if torch.cuda.is_available():
        model_kwargs["device_map"] = "auto"
    base = AutoModelForCausalLM.from_pretrained(base_path, **model_kwargs)
    model = PeftModel.from_pretrained(base, adapter_path, local_files_only=True)
    model.eval()
    return tokenizer, model


def _find_local_base_model(model_id):
    """Resolve a Hub ID to an already downloaded snapshot, independent of HF_HOME."""
    model_id = str(model_id or "").strip()
    if not model_id:
        return None
    direct = Path(model_id).expanduser()
    if direct.is_dir() and (direct / "config.json").is_file():
        return str(direct.resolve())

    parts = model_id.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return None
    project_copy = Path(__file__).resolve().parent.parent / "base_models" / f"{parts[0]}--{parts[1]}"
    if project_copy.is_dir() and (project_copy / "config.json").is_file():
        has_weights = any(item.is_file() for item in project_copy.glob("*.safetensors")) or any(
            item.is_file() for item in project_copy.glob("pytorch_model*.bin")
        )
        if has_weights:
            return str(project_copy.resolve())
    repo_folder = f"models--{parts[0]}--{parts[1]}"
    cache_roots = []
    for variable in ("HF_HUB_CACHE", "TRANSFORMERS_CACHE"):
        value = os.environ.get(variable, "").strip()
        if value:
            root = Path(value).expanduser()
            cache_roots.append(root if root.name == "hub" else root / "hub")
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        cache_roots.append(Path(hf_home).expanduser() / "hub")
    cache_roots.extend((
        Path.home() / ".cache" / "huggingface" / "hub",
        Path(__file__).resolve().parent.parent / "hf_cache" / "hub",
    ))

    snapshots = []
    seen = set()
    for root in cache_roots:
        try:
            cache = (root / repo_folder).resolve()
            if cache in seen:
                continue
            seen.add(cache)
            for snapshot in (cache / "snapshots").iterdir():
                has_weights = any(item.is_file() for item in snapshot.glob("*.safetensors")) or any(
                    item.is_file() for item in snapshot.glob("pytorch_model*.bin")
                )
                if snapshot.is_dir() and (snapshot / "config.json").is_file() and has_weights:
                    snapshots.append(snapshot)
        except OSError:
            continue
    if not snapshots:
        return None
    snapshots.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(snapshots[0].resolve())


@_lru_cache(maxsize=1)
def _cached_local_adapter(adapter_path):
    return _load_local_adapter(adapter_path)


def _local_finetuned_generate(adapter_path, system_prompt, prompt, timeout):
    import torch
    path = os.path.abspath(adapter_path)
    if not os.path.isfile(os.path.join(path, "adapter_config.json")):
        raise RuntimeError(f"Local fine-tuned model adapter was not found at {path}.")
    try:
        tokenizer, model = _cached_local_adapter(path)
        messages = [{"role": "system", "content": system_prompt or "You are a helpful specialist agent."}, {"role": "user", "content": prompt}]
        if not getattr(tokenizer, "chat_template", None):
            raise RuntimeError("This base model has no chat template. Fine-tune a chat or instruct base model.")
        encoded = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        device = next(model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with _LOCAL_MODEL_LOCK, torch.inference_mode():
            output = model.generate(**encoded, max_new_tokens=1200, do_sample=True, temperature=0.7, pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
        answer = tokenizer.decode(output[0][encoded["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"The local fine-tuned model could not respond: {str(exc)[:500]}") from exc
    if not answer:
        raise RuntimeError("The local fine-tuned model returned an empty response.")
    return answer
