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
- Local chat uploads for images, PDFs, and text/code files, plus downloadable Markdown replies and code blocks.
- An Agent Training area that accepts files or folders, creates downloadable role-specific Markdown learning guides, and includes those guides in later agent conversations.
- Optional public web search and page reading for team tasks, controlled per message from the team chat.
- Optional read-only Tor retrieval of home pages from exact v3 .onion hosts listed in `TOR_ALLOWED_ONION_HOSTS`; unrestricted onion discovery, link crawling, logins, and form submissions are not enabled.
- Controller-led orchestration: specialists take turns, read and respond to earlier agents' reports, and stream each contribution into the room before the controller summarizes.
- A larger, more compact team chat with a room-wide clear action that removes shared message history and unused attachment files for every connected viewer.
- SQLite-backed agent and chat-room data.
- Local WebSocket rooms for real-time messages and agent updates.

## Start locally

Use Python 3.11 or newer. Follow the full, ordered Windows setup, environment, API-key, model, room, and Cognito instructions in [HOW_TO_USE.txt](HOW_TO_USE.txt).

The short version is:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- Dashboard: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

Starter agents reuse their configured Ollama models when those are installed. Otherwise, the platform uses `DEFAULT_OLLAMA_MODEL` (`llama3.2:3b` by default) or an installed Ollama model. Run `ollama pull llama3.2:3b` once if you need the fallback, or import a Hugging Face model reference and set `HF_TOKEN` in `.env`.

## Authentication

Set a strong `JWT_SECRET` in `.env`. In local mode, an agent API key exchanges for a time-limited JWT at `POST /v1/tokens`; the API key can request replacement tokens until revoked. Set `AUTH_MODE=cognito` and the Cognito pool settings to verify Cognito access tokens instead. `AUTH_MODE=hybrid` accepts either kind during development. Keep the service bound to localhost unless you add production access controls and HTTPS.

## Current deployment scope

This version is for local Uvicorn use. Cognito JWT verification is configurable, but AWS resources such as API Gateway, Lambda, and WebSocket API are not provisioned by this project yet. Hugging Face model import registers a repository reference; the selected hosted provider runs inference, so the platform does not download model weights by itself.
