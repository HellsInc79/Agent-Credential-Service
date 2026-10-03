"""Text-generation adapters for local Ollama and Hugging Face models."""

import os
import requests


def generate(provider, model_id, system_prompt, prompt, timeout=180, images=None, api_key=None):
    provider = (provider or "").strip().lower()
    images = images or []
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
