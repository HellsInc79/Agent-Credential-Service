# models.py
from pydantic import BaseModel, Field

class BoardroomRequest(BaseModel):

    prompt: str

class AgentCreate(BaseModel):

    name: str

    role: str

    model: str

    system_prompt: str

    permissions: list[str] = Field(default_factory=list)


class KeyCreate(BaseModel):

    agent_id: str

    token_limit: int = Field(
        default=1000000,
        ge=1
    )


class TokenRequest(BaseModel):

    api_key: str


class ChatRequest(BaseModel):

    prompt: str

    agent_id: str | None = None

    system: str | None = None

    model: str | None = None

    conversation_id: str | None = None

class MemoryCreate(BaseModel):

    agent_name: str

    memory: str

class BoardroomHistoryRequest(
    BaseModel
):
    session_id: str

class HierarchyRequest(
    BaseModel
):
    prompt: str

class ToolRequest(BaseModel):

    tool_name: str

    argument: str

class DelegatedTaskCreate(
    BaseModel
):

    title: str

    assigned_agent: str

    details: str

class DelegatedTaskResult(
    BaseModel
):

    result: str

class AgentRegistryCreate(
    BaseModel
):

    name: str

    role: str

    model: str

    system_prompt: str

class WorkflowCreate(
    BaseModel
):

    name: str

    goal: str

class WorkflowStep(
    BaseModel
):

    assigned_agent: str

    objective: str

class OrchestrationRequest(
    BaseModel
):

    prompt: str

class PersistentAgentCreate(
    BaseModel
):

    name: str

    role: str

    model: str

    system_prompt: str

class DepartmentCreate(
    BaseModel
):

    name: str

    manager: str

class DepartmentMember(
    BaseModel
):

    agent_name: str

class PerformanceScore(
    BaseModel
):

    agent_name: str

    score: float

    notes: str = ""

class LessonCreate(
    BaseModel
):

    category: str

    lesson: str

    outcome: str

class AuditEventCreate(
    BaseModel
):

    event_type: str

    source: str

    details: str

class CompanySetup(
    BaseModel
):

    name: str

    mission: str

    ceo: str

class GoalCreate(
    BaseModel
):

    goal: str

class KnowledgeCreate(
    BaseModel
):

    title: str

    category: str

    content: str

class WorkflowExecution(
    BaseModel
):

    workflow_id: str

class RoomCreate(
    BaseModel
):

    room_name: str

class RoomMessage(
    BaseModel
):

    sender: str

    content: str

class VoteCreate(
    BaseModel
):

    proposal: str

class VoteCast(
    BaseModel
):

    agent_name: str

    score: int

    comment: str = ""

class AutonomousRequest(
    BaseModel
):

    objective: str

class EventCreate(
    BaseModel
):

    event_type: str

    source: str

    payload: str
