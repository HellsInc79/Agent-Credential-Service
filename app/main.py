"""Local-first multi-agent platform API served by Uvicorn."""

import asyncio
import mimetypes
import os
import requests
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

load_dotenv()

# Keep large model downloads in the user's local Windows profile by default,
# outside project folders that may be synchronized by OneDrive.
# Users can set HF_HOME in .env to choose a different cache location.
_default_hf_home = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AI_Agent_Credential_Service" / "hf_cache"
os.environ.setdefault("HF_HOME", str(_default_hf_home))

from app import database
from app.agent_runtime import generate_for_agent
from app.attachment_store import attachment_path, prepare_attachments, save_uploaded_files
from app.credential_vault import encrypt_secret
from app.platform_auth import issue_platform_token, require_auth, verify_bearer_token
from app.platform_orchestrator import run_orchestration
from app.platform_store import (
    authenticate_agent_key,
    agent_provider_credential_status,
    clear_room_messages,
    create_agent as store_create_agent,
    create_agent_key,
    delete_agent as store_delete_agent,
    delete_agent_provider_credential,
    delete_attachments,
    get_agent,
    get_agent_learning_file,
    get_attachment,
    get_room_messages,
    installed_ollama_models,
    import_model as store_import_model,
    init_platform_db,
    list_agents,
    list_agent_keys,
    list_agent_learning_files,
    list_models,
    list_rooms,
    migrate_legacy_agents,
    revoke_agent_key,
    save_message,
    set_agent_provider_credential,
    top_up_agent_tokens,
    seed_default_agents,
    update_agent,
)
from app.realtime import room_connections
from app.web_tools import search_public_web
from app.training import create_role_guides
from app.local_model_training import create_model_backup, delete_failed_job as delete_failed_local_training_job, hardware_status as local_training_status, list_models as list_finetuned_models, list_pretrained_base_models, get_jobs as list_local_training_jobs, get_base_model_restore, start_base_model_restore, set_training_device_mode, resume_interrupted_jobs, resume_training as resume_local_training, start_training as start_local_training
from app.local_tool_installer import list_installation_jobs as list_video_installations, start_installation as start_video_tool_installation
from app.onion_tools import search_authorized_onion_sources
from app.video_studio import VideoStudioError, blender_starter_script, get_job_path, get_video_asset_path, list_assets as list_video_assets, list_jobs as list_video_jobs, render_video, save_video_asset, tool_status as video_tool_status
from app import wan_video


_room_epochs = {}
_room_epochs_lock = threading.Lock()


def _room_epoch(room_name):
    with _room_epochs_lock:
        return _room_epochs.get(room_name, 0)


async def _broadcast_if_room_current(room_name, item, epoch):
    if _room_epoch(room_name) == epoch:
        await room_connections.broadcast(room_name, item)


def auth_mode():
    return os.getenv("AUTH_MODE", "local").strip().lower()


def management_auth(authorization: str | None = Header(default=None)):
    """Local mode is convenient for localhost setup; Cognito modes require a JWT."""
    mode = auth_mode()
    if mode == "local":
        return None
    if mode not in {"cognito", "hybrid"}:
        raise HTTPException(status_code=500, detail="AUTH_MODE must be local, cognito, or hybrid.")
    if not authorization:
        raise HTTPException(status_code=401, detail="Sign in with Cognito and send an access token.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authorization must use the Bearer scheme.")
    return verify_bearer_token(token)


def current_sender(claims):
    agent_id = claims.get("agent_id") or claims.get("sub")
    agent = get_agent(agent_id) if agent_id else None
    if agent and agent["enabled"]:
        return {"id": agent["id"], "name": agent["name"], "title": agent["title"]}
    return {
        "id": str(agent_id or "user"),
        "name": claims.get("name") or claims.get("username") or "User",
        "title": "User",
    }


def local_chat_claims(authorization: str | None = Header(default=None)):
    """Allow room chat without sign-in on the localhost-only development server."""
    if auth_mode() == "local" and not authorization:
        return {"sub": "local-user", "name": "You"}
    return require_auth(authorization)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    init_platform_db()
    migrate_legacy_agents()
    seed_default_agents()
    resume_interrupted_jobs()
    yield


app = FastAPI(
    title="Agent Team Platform",
    description="Local multi-agent registry, renewable API-key sessions, model catalog, orchestration, and room chat.",
    version="3.0.0",
    lifespan=lifespan,
)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    profession: str = Field(min_length=1, max_length=120)
    rank: str = Field(default="Specialist", min_length=1, max_length=60)
    provider: str = Field(default="ollama", pattern="^(ollama|huggingface|docker-model-runner|docker-agent|openai|local-finetune)$")
    model_id: str = Field(default="llama3.2:3b", min_length=1, max_length=300)
    system_prompt: str = Field(default="", max_length=6000)
    provider_api_key: str | None = Field(default=None, max_length=3000)

    @field_validator("name", "title", "profession", "rank", "model_id")
    @classmethod
    def strip_required_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    profession: str | None = Field(default=None, min_length=1, max_length=120)
    rank: str | None = Field(default=None, min_length=1, max_length=60)
    provider: str | None = Field(default=None, pattern="^(ollama|huggingface|docker-model-runner|docker-agent|openai|local-finetune)$")
    model_id: str | None = Field(default=None, min_length=1, max_length=300)
    system_prompt: str | None = Field(default=None, max_length=6000)
    enabled: bool | None = None


class ModelImport(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(pattern="^(ollama|huggingface|docker-model-runner|docker-agent|openai|local-finetune)$")
    model_id: str = Field(min_length=1, max_length=300)
    revision: str | None = Field(default=None, max_length=120)
    task: str = Field(default="text-generation", max_length=80)

    @field_validator("model_id")
    @classmethod
    def validate_model_reference(cls, value):
        value = value.strip()
        if not value or ".." in value or value.startswith(("http://", "https://")):
            raise ValueError("Use an Ollama model tag or a Hugging Face repository ID such as organization/model-name")
        if "://" in value:
            raise ValueError("Model ID must not be a URL")
        return value


class ApiKeyCreate(BaseModel):
    label: str = Field(default="Agent key", max_length=100)


class ProviderApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=3000)


class TokenTopUpRequest(BaseModel):
    amount: int = Field(ge=1, le=1_000_000_000)


class TokenRequest(BaseModel):
    api_key: str = Field(min_length=12, max_length=300)


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    room_name: str = Field(default="executive-board", min_length=1, max_length=80)
    agent_id: str | None = None
    use_web: bool = False
    provider: str | None = Field(default=None, pattern="^(ollama|huggingface|docker-model-runner|docker-agent|openai|local-finetune)$")
    model_id: str | None = Field(default=None, max_length=300)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class OrchestrationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    room_name: str = Field(default="executive-board", min_length=1, max_length=80)
    controller_id: str | None = None
    use_web: bool = False
    use_onion: bool = False
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class RoomMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    as_user: bool = False
    use_web: bool = False
    use_onion: bool = False
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class LocalModelAssign(BaseModel):
    model_id: str = Field(min_length=1, max_length=300)
    provider: str = Field(default="ollama", pattern="^(ollama|docker-model-runner|docker-agent)$")


class AttachmentPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=220)
    data_base64: str = Field(min_length=1, max_length=7_000_000)


class AttachmentUpload(BaseModel):
    files: list[AttachmentPayload] = Field(min_length=1, max_length=5)


class VideoRenderRequest(BaseModel):
    title: str = Field(default="Untitled episode", max_length=120)
    asset_ids: list[str] = Field(min_length=1, max_length=12)
    audio_asset_id: str | None = None
    image_seconds: int = Field(default=5, ge=1, le=60)


class WanVideoRequest(BaseModel):
    title: str = Field(default="AI generated video", max_length=120)
    prompt: str = Field(min_length=8, max_length=3000)
    negative_prompt: str = Field(default="", max_length=1200)
    width: int = Field(default=832, ge=256, le=1024)
    height: int = Field(default=480, ge=256, le=1024)
    frames: int = Field(default=33, ge=1, le=81)


class AgentTrainingRequest(BaseModel):
    files: list[AttachmentPayload] = Field(default_factory=list, max_length=50)
    agent_ids: list[str] = Field(default_factory=list, max_length=50)
    focus: str = Field(default="", max_length=3000)


class LocalModelTrainingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_model: str = Field(min_length=1, max_length=500)
    dataset_jsonl: str = Field(min_length=1, max_length=12_000_000)
    agent_id: str = Field(min_length=1, max_length=80)
    epochs: int = Field(default=3, ge=1, le=5)


class LocalTrainingSettingsUpdate(BaseModel):
    device_mode: str = Field(pattern="^(auto|cpu|gpu)$")


def public_agent(agent):
    if not agent:
        return None
    result = dict(agent)
    result.pop("system_prompt", None)
    return result


def _require_agent(agent_id):
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


def _add_room_event(item):
    saved = save_message(
        item["room_name"], item["sender_id"], item["sender_name"],
        item.get("sender_title", ""), item["content"], item.get("message_type", "message"),
        item.get("attachments", []),
    )
    return saved


async def _orchestrate_in_thread(request):
    loop = asyncio.get_running_loop()
    turn_epoch = _room_epoch(request.room_name)
    attachment_metadata, images, attachment_text = prepare_attachments(request.attachment_ids)

    def emit(item):
        with _room_epochs_lock:
            if _room_epochs.get(request.room_name, 0) != turn_epoch:
                return
            saved = _add_room_event(item)
        asyncio.run_coroutine_threadsafe(
            _broadcast_if_room_current(request.room_name, saved, turn_epoch), loop
        )

    return await asyncio.to_thread(
        run_orchestration,
        request.prompt,
        request.room_name,
        emit,
        request.controller_id,
        request.use_web,
        search_public_web,
        attachment_metadata,
        images,
        attachment_text,
        request.use_onion,
        search_authorized_onion_sources,
    )


async def _clear_room(room_name):
    with _room_epochs_lock:
        _room_epochs[room_name] = _room_epochs.get(room_name, 0) + 1
    cleared = clear_room_messages(room_name)
    for item in cleared["attachments"]:
        path = attachment_path(item)
        if path:
            path.unlink(missing_ok=True)
    await room_connections.broadcast(room_name, {
        "type": "room_cleared", "room_name": room_name,
        "cleared_count": cleared["message_count"],
    })
    return cleared["message_count"]


@app.get("/", response_class=HTMLResponse)
def dashboard():
    path = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
    if not path.is_file():
        raise HTTPException(status_code=500, detail="Dashboard template is missing.")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent-team-platform", "auth_mode": auth_mode()}


@app.get("/v1/agents")
def agents(include_disabled: bool = False, _: dict | None = Depends(management_auth)):
    return [public_agent(agent) for agent in list_agents(include_disabled=include_disabled)]


@app.get("/v1/agents/{agent_id}")
def agent_details(agent_id: str, _: dict | None = Depends(management_auth)):
    return public_agent(_require_agent(agent_id))


@app.post("/v1/agents", status_code=201)
def register_agent(body: AgentCreate, _: dict | None = Depends(management_auth)):
    if body.provider_api_key and body.provider not in {"openai", "huggingface", "docker-agent"}:
        raise HTTPException(status_code=422, detail="This provider does not use a provider API key.")
    if body.provider_api_key and len(body.provider_api_key.strip()) < 8:
        raise HTTPException(status_code=422, detail="Provider API keys must contain at least 8 characters.")
    encrypted_key = None
    if body.provider_api_key:
        try:
            encrypted_key = encrypt_secret(body.provider_api_key.strip())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    agent = store_create_agent(body.model_dump())
    if encrypted_key:
        set_agent_provider_credential(agent["id"], encrypted_key, body.provider_api_key.strip()[-4:])
    key = create_agent_key(agent["id"])
    return {
        "agent": public_agent(agent),
        "api_key": key["api_key"],
        "key_warning": "Copy this key now. It is stored as a hash and cannot be shown again.",
    }


@app.patch("/v1/agents/{agent_id}")
def edit_agent(agent_id: str, body: AgentUpdate, _: dict = Depends(management_auth)):
    _require_agent(agent_id)
    changes = {key: value for key, value in body.model_dump(exclude_unset=True).items() if value is not None}
    agent = update_agent(agent_id, changes)
    return {"agent": public_agent(agent)}


@app.delete("/v1/agents/{agent_id}")
def disable_agent(agent_id: str, _: dict = Depends(management_auth)):
    _require_agent(agent_id)
    update_agent(agent_id, {"enabled": False})
    return {"status": "disabled", "agent_id": agent_id}


@app.delete("/v1/agents/{agent_id}/permanent")
def remove_agent(agent_id: str, _: dict | None = Depends(management_auth)):
    if not store_delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found.")
    return {"status": "removed", "agent_id": agent_id, "room_history_preserved": True}


@app.post("/v1/agents/{agent_id}/api-keys", status_code=201)
def new_agent_key(agent_id: str, body: ApiKeyCreate, _: dict = Depends(management_auth)):
    _require_agent(agent_id)
    key = create_agent_key(agent_id, body.label)
    if not key:
        raise HTTPException(status_code=409, detail="Agent is disabled; enable it before creating another key.")
    return {**key, "label": body.label, "key_warning": "Copy this key now. It is stored as a hash and cannot be shown again."}


@app.get("/v1/agents/{agent_id}/api-keys")
def agent_keys(agent_id: str, _: dict = Depends(management_auth)):
    _require_agent(agent_id)
    return list_agent_keys(agent_id)


@app.delete("/v1/agents/{agent_id}/api-keys/{key_id}")
def disable_agent_key(agent_id: str, key_id: str, _: dict = Depends(management_auth)):
    _require_agent(agent_id)
    if not revoke_agent_key(agent_id, key_id):
        raise HTTPException(status_code=404, detail="Active key not found for this agent.")
    return {"status": "revoked", "key_id": key_id}


@app.get("/v1/agents/{agent_id}/provider-key")
def provider_key_status(agent_id: str, _: dict | None = Depends(management_auth)):
    _require_agent(agent_id)
    return agent_provider_credential_status(agent_id)


@app.put("/v1/agents/{agent_id}/provider-key")
def save_provider_key(agent_id: str, body: ProviderApiKeyRequest, _: dict | None = Depends(management_auth)):
    agent = _require_agent(agent_id)
    if agent["provider"] not in {"openai", "huggingface", "docker-agent"}:
        raise HTTPException(status_code=409, detail="This agent's provider does not use a provider API key.")
    try:
        encrypted_key = encrypt_secret(body.api_key.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not set_agent_provider_credential(agent_id, encrypted_key, body.api_key.strip()[-4:]):
        raise HTTPException(status_code=404, detail="Agent not found.")
    return {"status": "saved", **agent_provider_credential_status(agent_id)}


@app.delete("/v1/agents/{agent_id}/provider-key")
def remove_provider_key(agent_id: str, _: dict | None = Depends(management_auth)):
    _require_agent(agent_id)
    delete_agent_provider_credential(agent_id)
    return {"status": "removed"}


@app.post("/v1/agents/{agent_id}/tokens/top-up")
def top_up_tokens(agent_id: str, body: TokenTopUpRequest, _: dict | None = Depends(management_auth)):
    agent = top_up_agent_tokens(agent_id, body.amount)
    if not agent:
        if not get_agent(agent_id):
            raise HTTPException(status_code=404, detail="Agent not found.")
        raise HTTPException(status_code=409, detail="The token allowance reached its maximum supported balance.")
    return {"agent_id": agent_id, "token_balance": agent["token_balance"], "tokens_used": agent["tokens_used"]}


@app.post("/v1/tokens")
def exchange_api_key(body: TokenRequest):
    if auth_mode() == "cognito":
        raise HTTPException(status_code=409, detail="Cognito mode uses Cognito-issued access tokens. Use your user-pool OAuth token endpoint.")
    return issue_platform_token(body.api_key)


@app.get("/v1/models")
def models(_: dict | None = Depends(management_auth)):
    return list_models()


@app.get("/v1/local/models")
def local_models(_: dict | None = Depends(management_auth)):
    ollama_models = sorted(installed_ollama_models())
    docker_model_base = os.getenv("DOCKER_MODEL_RUNNER_BASE_URL", "http://127.0.0.1:12434/engines/v1").rstrip("/")
    docker_agent_base = os.getenv("DOCKER_AGENT_BASE_URL", "http://127.0.0.1:8083/v1").rstrip("/")
    docker_agent_key = os.getenv("DOCKER_AGENT_API_KEY", "").strip()

    def remote_models(url, api_key=""):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.get(url, headers=headers, timeout=2)
            response.raise_for_status()
            return sorted({item.get("id") for item in response.json().get("data", []) if item.get("id")})
        except (requests.RequestException, ValueError, AttributeError):
            return []

    docker_models = remote_models(f"{docker_model_base}/models")
    docker_agents = remote_models(f"{docker_agent_base}/models", docker_agent_key)
    return {
        "ollama_models": ollama_models,
        "docker_model_runner_models": docker_models,
        "docker_agent_models": docker_agents,
        "ollama_reachable": bool(ollama_models),
        "docker_model_runner_reachable": bool(docker_models),
        "docker_agent_reachable": bool(docker_agents),
    }


@app.get("/v1/video/status")
def video_status(_: dict | None = Depends(management_auth)):
    return video_tool_status()


@app.get("/v1/video/generator/status")
def video_generator_status(_: dict | None = Depends(management_auth)):
    return wan_video.status()


@app.post("/v1/video/generator/start", status_code=202)
def start_video_generator(_: dict | None = Depends(management_auth)):
    try:
        return wan_video.start_engine()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/video/generator/jobs", status_code=202)
def create_video_generation(body: WanVideoRequest, _: dict | None = Depends(management_auth)):
    try:
        return wan_video.start_generation(body.title, body.prompt, body.negative_prompt, body.width, body.height, body.frames)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/v1/video/generator/jobs")
def video_generation_jobs(_: dict | None = Depends(management_auth)):
    return {"jobs": wan_video.list_jobs()}


@app.get("/v1/video/generator/jobs/{job_id}")
def video_generation_job(job_id: str, _: dict | None = Depends(management_auth)):
    job = wan_video.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video generation job not found.")
    return job


@app.get("/v1/video/installations")
def video_installation_jobs(_: dict | None = Depends(management_auth)):
    return list_video_installations()


@app.post("/v1/video/installations/{tool}", status_code=202)
def install_video_tool(tool: str, _: dict | None = Depends(management_auth)):
    try:
        return start_video_tool_installation(tool.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/video/assets")
def video_assets(_: dict | None = Depends(management_auth)):
    return {"assets": list_video_assets()}


@app.post("/v1/video/assets", status_code=201)
async def upload_video_asset(request: Request, filename: str, _: dict | None = Depends(management_auth)):
    try:
        return {"asset": await save_video_asset(filename, request.stream())}
    except VideoStudioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/v1/video/assets/{asset_id}/file")
def play_video_asset(asset_id: str, _: dict | None = Depends(management_auth)):
    item = get_video_asset_path(asset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video clip not found. Refresh the media list and try again.")
    path, filename = item
    return FileResponse(path, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream", filename=filename, content_disposition_type="inline")


@app.post("/v1/video/render", status_code=201)
def render_video_project(body: VideoRenderRequest, _: dict | None = Depends(management_auth)):
    try:
        return render_video(body.title, body.asset_ids, body.image_seconds, body.audio_asset_id)
    except VideoStudioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/v1/video/jobs")
def video_jobs(_: dict | None = Depends(management_auth)):
    return {"jobs": list_video_jobs()}


@app.get("/v1/video/jobs/{job_id}/file")
def video_job_file(job_id: str, _: dict | None = Depends(management_auth)):
    path = get_job_path(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="Rendered video not found.")
    return FileResponse(path, media_type="video/mp4", filename=path.name, content_disposition_type="inline")


@app.get("/v1/video/blender-starter")
def download_blender_starter(_: dict | None = Depends(management_auth)):
    return Response(content=blender_starter_script(), media_type="text/x-python", headers={"Content-Disposition": "attachment; filename=series_title_card_starter.py"})


@app.post("/v1/attachments", status_code=201)
def upload_attachments(body: AttachmentUpload, _: dict | None = Depends(management_auth)):
    return {"attachments": save_uploaded_files([item.model_dump() for item in body.files])}


@app.get("/v1/attachments/{attachment_id}")
def download_attachment(attachment_id: str, _: dict | None = Depends(management_auth)):
    item = get_attachment(attachment_id)
    if not item:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    path = attachment_path(item)
    if not path:
        raise HTTPException(status_code=404, detail="The local attachment file is missing.")
    return FileResponse(path, media_type=item["content_type"])


@app.post("/v1/training/generate")
async def generate_agent_training(body: AgentTrainingRequest, _: dict | None = Depends(management_auth)):
    uploads = []
    try:
        batch = []
        batch_size = 0
        for file in body.files:
            encoded_size = len(file.data_base64) * 3 // 4
            if batch and (len(batch) >= 5 or batch_size + encoded_size > 10 * 1024 * 1024):
                uploads.extend(save_uploaded_files([item.model_dump() for item in batch]))
                batch, batch_size = [], 0
            batch.append(file)
            batch_size += encoded_size
        if batch:
            uploads.extend(save_uploaded_files([item.model_dump() for item in batch]))
        attachment_ids = [item["id"] for item in uploads]
        results = await asyncio.to_thread(
            create_role_guides, attachment_ids, body.focus, body.agent_ids or None
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        # Uploaded source files are temporary inputs; generated role guides persist in SQLite.
        for item in delete_attachments([entry["id"] for entry in uploads]):
            path = attachment_path(item)
            if path:
                path.unlink(missing_ok=True)
    completed = sum(result["status"] == "created" for result in results)
    return {
        "status": "complete" if completed == len(results) else "partial" if completed else "error",
        "created": completed,
        "results": results,
    }


@app.get("/v1/local-training/status")
def local_model_training_environment(_: dict | None = Depends(management_auth)):
    return local_training_status()


@app.put("/v1/local-training/settings")
def update_local_training_settings(body: LocalTrainingSettingsUpdate, _: dict | None = Depends(management_auth)):
    set_training_device_mode(body.device_mode)
    return local_training_status()


@app.get("/v1/local-training/models")
def local_model_training_models(_: dict | None = Depends(management_auth)):
    return list_finetuned_models()


@app.post("/v1/local-training/base-model-restores", status_code=202)
def restore_local_training_base_model(body: dict, _: dict | None = Depends(management_auth)):
    try:
        return start_base_model_restore(body.get("model_id", ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/local-training/base-model-restores/{restore_id}")
def local_training_base_model_restore_status(restore_id: str, _: dict | None = Depends(management_auth)):
    result = get_base_model_restore(restore_id)
    if not result:
        raise HTTPException(status_code=404, detail="That base-model restore job was not found.")
    return result


@app.get("/v1/local-training/base-models")
def local_model_training_base_models(_: dict | None = Depends(management_auth)):
    return list_pretrained_base_models()


@app.get("/v1/local-training/models/{model_id}/backup")
def download_local_model_backup(model_id: str, _: dict | None = Depends(management_auth)):
    try:
        archive = create_model_backup(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(archive, media_type="application/zip", filename=archive.name)


@app.get("/v1/local-training/jobs")
def local_model_training_jobs(_: dict | None = Depends(management_auth)):
    return list_local_training_jobs()


@app.post("/v1/local-training/jobs", status_code=202)
def create_local_model_training_job(body: LocalModelTrainingRequest, _: dict | None = Depends(management_auth)):
    try:
        return start_local_training(body.name, body.base_model, body.dataset_jsonl, body.epochs, body.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/local-training/jobs/{job_id}/resume", status_code=202)
def resume_local_model_training_job(job_id: str, _: dict | None = Depends(management_auth)):
    try:
        return resume_local_training(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/v1/local-training/jobs/{job_id}")
def delete_local_model_training_job(job_id: str, _: dict | None = Depends(management_auth)):
    try:
        return delete_failed_local_training_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/training/files")
def agent_training_files(_: dict | None = Depends(management_auth)):
    return list_agent_learning_files()


@app.get("/v1/training/files/{file_id}")
def download_agent_training_file(file_id: str, _: dict | None = Depends(management_auth)):
    item = get_agent_learning_file(file_id)
    if not item:
        raise HTTPException(status_code=404, detail="Training guide not found.")
    filename = "".join(char for char in item["filename"] if char.isalnum() or char in "-_. ").strip() or "role-guide.md"
    safe_filename = filename.replace('"', "")
    return Response(
        content=item["content"], media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@app.post("/v1/local/assign-model")
def assign_local_model(body: LocalModelAssign, _: dict | None = Depends(management_auth)):
    if body.provider == "ollama":
        installed = installed_ollama_models()
    else:
        base_url = os.getenv(
            "DOCKER_MODEL_RUNNER_BASE_URL" if body.provider == "docker-model-runner" else "DOCKER_AGENT_BASE_URL",
            "http://127.0.0.1:12434/engines/v1" if body.provider == "docker-model-runner" else "http://127.0.0.1:8083/v1",
        ).rstrip("/")
        headers = {}
        if body.provider == "docker-agent" and os.getenv("DOCKER_AGENT_API_KEY", "").strip():
            headers["Authorization"] = f"Bearer {os.getenv('DOCKER_AGENT_API_KEY').strip()}"
        try:
            response = requests.get(f"{base_url}/models", headers=headers, timeout=2)
            response.raise_for_status()
            installed = {item.get("id") for item in response.json().get("data", []) if item.get("id")}
        except (requests.RequestException, ValueError, AttributeError):
            installed = set()
    if not installed:
        raise HTTPException(status_code=503, detail="That local model service is not reachable or has no available models. Start it and refresh the model list.")
    model_id = body.model_id.strip()
    if model_id not in installed:
        raise HTTPException(status_code=404, detail="That model is not available from the selected local service. Refresh the model list.")
    team = list_agents()
    for agent in team:
        update_agent(agent["id"], {"provider": body.provider, "model_id": model_id})
    return {"status": "updated", "provider": body.provider, "model_id": model_id, "agents_updated": len(team)}


@app.post("/v1/models/import", status_code=201)
def import_model(body: ModelImport, _: dict = Depends(management_auth)):
    model, created = store_import_model(body.model_dump())
    return {"model": model, "created": created, "note": "This registers the model reference; its selected local service or Hugging Face supplies it at run time."}


@app.get("/v1/rooms")
def rooms(_: dict | None = Depends(management_auth)):
    return list_rooms()


@app.get("/v1/rooms/{room_name}/messages")
def room_history(room_name: str, _: dict = Depends(local_chat_claims)):
    return get_room_messages(room_name)


@app.delete("/v1/rooms/{room_name}/messages")
async def clear_room(room_name: str, _: dict = Depends(local_chat_claims)):
    count = await _clear_room(room_name)
    return {"status": "cleared", "room_name": room_name, "cleared_count": count}


@app.post("/v1/rooms/{room_name}/messages", status_code=201)
async def post_room_message(room_name: str, body: RoomMessageRequest, claims: dict = Depends(local_chat_claims)):
    sender = current_sender(claims)
    if body.as_user:
        sender = {"id": f"user:{sender['id']}", "name": "You", "title": "User"}
    item = {
        "room_name": room_name,
        "sender_id": sender["id"],
        "sender_name": sender["name"],
        "sender_title": sender["title"],
        "content": body.content,
        "message_type": "message",
    }
    saved = _add_room_event(item)
    await room_connections.broadcast(room_name, saved)
    return saved


@app.post("/v1/rooms/{room_name}/chat")
async def room_chat(room_name: str, body: RoomMessageRequest, claims: dict = Depends(local_chat_claims)):
    """Post a user turn and have the controller coordinate the team in the room."""
    request = OrchestrationRequest(
        prompt=body.content, room_name=room_name, use_web=body.use_web, use_onion=body.use_onion,
        attachment_ids=body.attachment_ids,
    )
    try:
        return await _orchestrate_in_thread(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/chat")
async def chat(body: ChatRequest, claims: dict = Depends(local_chat_claims)):
    sender = current_sender(claims)
    target = _require_agent(body.agent_id or (claims.get("agent_id") if claims.get("iss") == "local-agent-platform" else ""))
    if not target["enabled"]:
        raise HTTPException(status_code=409, detail="This agent is disabled. Enable it before sending another prompt.")
    attachment_metadata, images, attachment_text = prepare_attachments(body.attachment_ids)
    prompt = body.prompt + (f"\n\n{attachment_text}" if attachment_text else "")
    user_message = {
        "room_name": body.room_name, "sender_id": f"user:{sender['id']}", "sender_name": "You",
        "sender_title": "User", "content": body.prompt, "message_type": "user",
        "attachments": attachment_metadata,
    }
    saved = _add_room_event(user_message)
    await room_connections.broadcast(body.room_name, saved)
    web_context = ""
    web_search = {"status": "disabled", "error": ""}
    if body.use_web:
        try:
            web_context = await asyncio.to_thread(search_public_web, body.prompt)
            web_search["status"] = "no_results" if web_context.startswith("Web search returned no readable public pages") else "complete"
        except Exception as exc:
            web_search = {"status": "failed", "error": f"Public web search could not complete: {str(exc)[:300]}"}
    try:
        answer = await asyncio.to_thread(
            generate_for_agent, target, prompt, images,
            body.provider or target["provider"], body.model_id or target["model_id"],
            web_context,
        )
    except Exception as exc:
        detail = str(exc)
        error_event = _add_room_event({
            "room_name": body.room_name, "sender_id": target["id"], "sender_name": target["name"],
            "sender_title": target["title"], "content": detail, "message_type": "error",
        })
        await room_connections.broadcast(body.room_name, error_event)
        raise HTTPException(status_code=503, detail=detail) from exc
    response = _add_room_event({
        "room_name": body.room_name, "sender_id": target["id"], "sender_name": target["name"],
        "sender_title": target["title"], "content": answer, "message_type": "agent",
    })
    await room_connections.broadcast(body.room_name, response)
    return {"agent": public_agent(target), "response": answer, "room_name": body.room_name, "web_search": web_search}


@app.post("/v1/orchestrate")
async def orchestrate(body: OrchestrationRequest, _: dict = Depends(local_chat_claims)):
    try:
        return await _orchestrate_in_thread(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.websocket("/ws")
async def websocket_room(websocket: WebSocket, room: str = "executive-board"):
    token = websocket.headers.get("authorization", "")
    scheme, _, bearer = token.partition(" ")
    if scheme.lower() == "bearer" and bearer:
        access_token = bearer
    else:
        protocols = websocket.scope.get("subprotocols", [])
        access_token = protocols[1] if len(protocols) > 1 and protocols[0] == "bearer" else ""
    if auth_mode() == "local" and not access_token:
        claims = {"sub": "local-user", "name": "You"}
    else:
        try:
            claims = verify_bearer_token(access_token) if access_token else None
        except HTTPException:
            claims = None
    if not claims:
        await websocket.close(code=4401, reason="Valid bearer access token required")
        return

    selected_protocol = "bearer" if "bearer" in websocket.scope.get("subprotocols", []) else None
    await room_connections.connect(room, websocket, selected_protocol)
    try:
        await websocket.send_json({"type": "history", "room": room, "messages": get_room_messages(room)})
        while True:
            payload = await websocket.receive_json()
            kind = payload.get("type", "message") if isinstance(payload, dict) else ""
            if kind == "clear":
                count = await _clear_room(room)
                await websocket.send_json({"type": "room_clear_complete", "cleared_count": count})
                continue
            if kind == "orchestrate":
                request = OrchestrationRequest(
                    prompt=payload.get("content", ""), room_name=room,
                    use_web=bool(payload.get("use_web", False)),
                    use_onion=bool(payload.get("use_onion", False)),
                    attachment_ids=payload.get("attachment_ids", []),
                )
                try:
                    result = await _orchestrate_in_thread(request)
                    await websocket.send_json({"type": "orchestration_complete", "result": result})
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                continue
            if kind == "team_chat":
                content = str(payload.get("content", "")).strip() if isinstance(payload, dict) else ""
                if not content or len(content) > 12000:
                    await websocket.send_json({"type": "error", "message": "Enter a message (up to 12,000 characters)."})
                    continue
                try:
                    await _orchestrate_in_thread(OrchestrationRequest(
                        prompt=content, room_name=room,
                        use_web=bool(payload.get("use_web", False)),
                        use_onion=bool(payload.get("use_onion", False)),
                        attachment_ids=payload.get("attachment_ids", []),
                    ))
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                continue
            if kind != "message" or not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "message": "Send {type: 'message', content: '...'} or {type: 'orchestrate', content: '...'}"})
                continue
            content = str(payload.get("content", "")).strip()
            if not content:
                await websocket.send_json({"type": "error", "message": "Message cannot be empty."})
                continue
            if len(content) > 12000:
                await websocket.send_json({"type": "error", "message": "Message is too long (12,000 character limit)."})
                continue
            sender = current_sender(claims)
            if payload.get("as_user"):
                sender = {"id": f"user:{sender['id']}", "name": "You", "title": "User"}
            saved = _add_room_event({
                "room_name": room, "sender_id": sender["id"], "sender_name": sender["name"],
                "sender_title": sender["title"], "content": content, "message_type": "message",
            })
            await room_connections.broadcast(room, saved)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": f"Room connection ended: {exc}"})
        except Exception:
            pass
    finally:
        await room_connections.disconnect(room, websocket)
