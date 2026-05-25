from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class UserMessageItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["userMessage"] = "userMessage"
    text: str


class AgentMessageItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["agentMessage"] = "agentMessage"
    text: str


class CommandExecutionItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["commandExecution"] = "commandExecution"
    command: str
    cwd: str
    status: Literal[
        "pending", "approved", "denied", "running", "completed", "failed"
    ] = "pending"
    output: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    tool_call_id: str | None = None
    tool_arguments: str | None = None
    backend: Literal["local", "docker", "ssh"] | None = None
    container_id: str | None = None
    image: str | None = None
    remote_host: str | None = None
    remote_user: str | None = None


class FileChangeItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["fileChange"] = "fileChange"
    path: str
    status: Literal["pending", "approved", "denied", "completed", "failed"] = "pending"
    summary: str | None = None
    tool_call_id: str | None = None
    tool_arguments: str | None = None
    content: str | None = None
    change_type: Literal["update", "add", "delete", "overwrite"] | None = None
    diff_snippet: str | None = None


class ContextCompactionItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["contextCompaction"] = "contextCompaction"
    summarized_items: int | None = None


class SkillActivationItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["skillActivation"] = "skillActivation"
    skills: list[str] = Field(default_factory=list)


class McpToolCallItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["mcpToolCall"] = "mcpToolCall"
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "pending", "approved", "denied", "running", "completed", "failed"
    ] = "pending"
    output: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    tool_call_id: str | None = None


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["webSearch"] = "webSearch"
    query: str
    results: list[WebSearchResult] = Field(default_factory=list)
    status: Literal["pending", "approved", "denied", "completed", "failed"] = "pending"
    tool_call_id: str | None = None
    tool_arguments: str | None = None
    error: str | None = None


class CollabSpawnItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["collabSpawn"] = "collabSpawn"
    worker_thread_id: str
    task: str
    status: Literal["running", "completed", "failed"] = "running"
    summary: str | None = None
    title: str | None = None
    model: str | None = None
    execution_backend: str | None = None
    tool_call_id: str | None = None
    tool_arguments: str | None = None


class CollabWorkerItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["collabWorker"] = "collabWorker"
    worker_id: str
    worker_thread_id: str
    parent_thread_id: str
    task: str
    depth: int = 0
    status: Literal["queued", "running", "completed", "failed", "timed_out"] = "queued"
    summary: str | None = None
    title: str | None = None
    model: str | None = None
    execution_backend: str | None = None
    tool_call_id: str | None = None
    tool_arguments: str | None = None


class WorkspaceSyncItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["workspaceSync"] = "workspaceSync"
    direction: Literal["push", "pull"]
    transport: Literal["rsync", "scp"]
    status: Literal["completed", "failed", "skipped"] = "skipped"
    summary: str = ""
    files: int | None = None
    bytes_transferred: int | None = None
    duration_ms: int | None = None


Item = Annotated[
    Union[
        UserMessageItem,
        AgentMessageItem,
        CommandExecutionItem,
        FileChangeItem,
        ContextCompactionItem,
        SkillActivationItem,
        McpToolCallItem,
        WebSearchItem,
        CollabSpawnItem,
        CollabWorkerItem,
        WorkspaceSyncItem,
    ],
    Field(discriminator="type"),
]


class Turn(BaseModel):
    id: str = Field(default_factory=new_id)
    status: Literal["running", "completed", "cancelled", "failed"] = "running"
    items: list[Item] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class Thread(BaseModel):
    id: str = Field(default_factory=new_id)
    cwd: str
    model: str
    repo_root: str | None = None
    forked_from: str | None = None
    title: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    turns: list[Turn] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def display_label(self) -> str:
        base = self.title or self.id[:8] + "..."
        if self.forked_from:
            return f"{base} (fork of {self.forked_from[:8]}...)"
        return base

    def last_user_message_preview(self, max_len: int = 80) -> str:
        for turn in reversed(self.turns):
            for item in reversed(turn.items):
                if item.type == "userMessage":
                    text = item.text.replace("\n", " ")
                    if len(text) > max_len:
                        return text[: max_len - 3] + "..."
                    return text
        return "(no messages)"


def parse_item(data: dict[str, Any]) -> Item | None:
    item_type = data.get("type")
    mapping = {
        "userMessage": UserMessageItem,
        "agentMessage": AgentMessageItem,
        "commandExecution": CommandExecutionItem,
        "fileChange": FileChangeItem,
        "contextCompaction": ContextCompactionItem,
        "skillActivation": SkillActivationItem,
        "mcpToolCall": McpToolCallItem,
        "webSearch": WebSearchItem,
        "collabSpawn": CollabSpawnItem,
        "collabWorker": CollabWorkerItem,
        "workspaceSync": WorkspaceSyncItem,
    }
    cls = mapping.get(item_type)
    if cls is None:
        return None
    return cls.model_validate(data)
