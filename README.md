# Agent Team Platform

A local-first Python platform for a team of AI agents with named jobs and ranks. Uvicorn serves the dashboard and API. Agents can use local Ollama models or Hugging Face Inference Providers, work under a controller agent, and share persistent room messages over HTTP and WebSocket.

## Main capabilities

- Agent registry fields: ID, name, title, profession, rank, provider, model ID, and instructions.
- Agent registry controls to enable, disable, and permanently remove agents; removal revokes their API keys and preserves room history.
- Per-agent API keys. Only a SHA-256 hash is stored; the plaintext key is shown once.
- Encrypted, per-agent OpenAI, Hugging Face, or Docker Agent provider API keys. Secrets are never sent back to the dashboard after saving.
- Each agent starts with a one-billion-token estimated local inference allowance, can be topped up from its registry card, and is charged an approximate prompt/response token count per successful run.
- Renewable, short-lived JWT access tokens so an agent can continue running by exchanging its API key when a token expires. The default key has no token-count limit.
- Optional verification of Amazon Cognito access tokens through the pool's public JWKS endpoint.
- Ollama and Hugging Face text generation, with an import endpoint for registering model references.
- Local Ollama and Docker model/agent pickers that can assign an installed local model or Docker Agent to active agents.
- Hugging Face providers accept Hub IDs and Hugging Face cache folder paths; local Transformers model folders can run directly from disk. New Hugging Face downloads use `%LOCALAPPDATA%\AI_Agent_Credential_Service\hf_cache` by default to keep large weights outside cloud-synced project folders (override with `HF_HOME` in `.env`).
- Local chat uploads for images, PDFs, and text/code files, plus downloadable Markdown replies and code blocks.
- Local Video Studio with a large playback window for rendered episodes and source clips, plus automatic detection of bundled FFmpeg/FFprobe and project-local downloads of FFmpeg/FFprobe and portable Blender.
- An Agent Training area that accepts files or folders, creates downloadable role-specific Markdown learning guides, and includes those guides in later agent conversations.
- Local LoRA fine-tuning of pretrained chat models into private adapters, saved in this project with resumable checkpoints and controls to remove failed jobs.
- Fine-tuning includes a base-model picker for cached Hugging Face chat models and common instruct models from the Hub; cached models load from local snapshots, while uncached selections download on the first training run.
- Fine-tuned adapters stay small and use their pretrained base model at reply time. Use **Restore base model** to copy a valid local cache into the project's `base_models/` folder or download it there once; training and inference then use that stable local copy.
- A partially downloaded base model stays in `base_models/.staging/` so a retry can resume. Finished base models are separate from adapters under `trained_models/` and are reusable across training jobs and agents.
- A saved CPU / NVIDIA GPU fine-tuning switch. Install the matching optional packages with `requirements-training-cpu.txt` or `requirements-training-gpu.txt` inside the project `.venv`; individual jobs remember their selected device mode.
- Optional public web search and page reading for direct agent replies and team tasks, controlled per message from chat.
- Optional read-only Tor research from configured v3 .onion hosts, with linked address discovery limited to separately configured source hosts; discovered hosts are not visited automatically.
- Controller-led orchestration: specialists take turns, read and respond to earlier agents' reports, and stream each contribution into the room before the controller summarizes.
- A larger, more compact team chat with a room-wide clear action that removes shared message history and unused attachment files for every connected viewer.
- SQLite-backed agent and chat-room data.
- Local WebSocket rooms for real-time messages and agent updates.

## Start locally

Use Python 3.11 or newer. Follow the full, ordered Windows setup, environment, API-key, model, room, and Cognito instructions in [HOW_TO_USE.txt](HOW_TO_USE.txt).

On Windows, the setup script creates the private runtime folders, a project `.venv`, installs application packages, and copies `.env.example` to `.env`:

```powershell
.\setup.ps1
```

For optional local fine-tuning packages, select one device:

```powershell
.\setup.ps1 -TrainingDevice GPU
# or
.\setup.ps1 -TrainingDevice CPU
```

Setup does not download model weights. After setup, edit `.env`, replace `JWT_SECRET` with a private random value of at least 32 characters, then start Uvicorn:

The short version is:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The local data folders (`uploads/`, `video_studio/`, `trained_models/`, `base_models/`, `hf_cache/`, and `tools/runtime/`) are created by setup and excluded from Git. They hold uploads, generated videos, model adapters and checkpoints, downloaded base models, caches, and installed tool files. Keep `.env` and `agents.db` local as well.

Then open:

- Dashboard: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

Starter agents reuse their configured Ollama models when those are installed. Otherwise, the platform uses `DEFAULT_OLLAMA_MODEL` (`llama3.2:3b` by default) or an installed Ollama model. Run `ollama pull llama3.2:3b` once if you need the fallback, or import a Hugging Face model reference and set `HF_TOKEN` in `.env`.

### Optional local fine-tuning packages

The standard application requirements do not install the large machine-learning packages. From the project folder in PowerShell, install one of these into this project's virtual environment:

```powershell
# NVIDIA GPU with CUDA
.\.venv\Scripts\python.exe -m pip install -r requirements-training-gpu.txt

# CPU only
.\.venv\Scripts\python.exe -m pip install -r requirements-training-cpu.txt
```

Run these commands from this project folder. Always install into this project's `.venv`; a different environment such as `C:\Windows\System32\.venv` is not used by the project server. If the GPU install says CPU PyTorch is already satisfied, replace it directly with the CUDA wheel:

```powershell
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cu130 "torch>=2.4,<3"
.\.venv\Scripts\python.exe -c "import sys,torch; print('Python:',sys.executable); print('PyTorch:',torch.__version__); print('CUDA build:',torch.version.cuda); print('GPU ready:',torch.cuda.is_available()); print('GPU:',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'not detected')"
```

The check must report this project's `.venv`, a PyTorch version ending in `+cu...`, a CUDA build, and `GPU ready: True`. In Fine-tune a local model, turn on **Use the NVIDIA GPU** to use CUDA or turn it off to force CPU for new jobs. The app status also displays the Python path, PyTorch build, and CUDA build it actually sees. Restart Uvicorn after installing or changing PyTorch packages. Each job keeps the device choice it started with. The NVIDIA requirements file uses PyTorch's CUDA 13.0 wheel source. Use the official [PyTorch installer selector](https://pytorch.org/get-started/locally/) to choose another supported CUDA wheel if your driver or GPU needs a different version.

## Authentication

Set a strong `JWT_SECRET` in `.env`. In local mode, an agent API key exchanges for a time-limited JWT at `POST /v1/tokens`; the API key can request replacement tokens until revoked. Set `AUTH_MODE=cognito` and the Cognito pool settings to verify Cognito access tokens instead. `AUTH_MODE=hybrid` accepts either kind during development. Keep the service bound to localhost unless you add production access controls and HTTPS.

## Current deployment scope

This version is for local Uvicorn use. Cognito JWT verification is configurable, but AWS resources such as API Gateway, Lambda, and WebSocket API are not provisioned by this project yet. Hugging Face model import registers a repository reference; the selected hosted provider runs inference, so the platform does not download model weights by itself.
