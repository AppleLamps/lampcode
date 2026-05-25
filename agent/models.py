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


class FileChangeItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["fileChange"] = "fileChange"
    path: str
    status: Literal["pending", "approved", "denied", "completed", "failed"] = "pending"
    summary: str | None = None
    tool_call_id: str | None = None
    tool_arguments: str | None = None
    content: str | None = None


class ContextCompactionItem(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["contextCompaction"] = "contextCompaction"


Item = Annotated[
    Union[
        UserMessageItem,
        AgentMessageItem,
        CommandExecutionItem,
        FileChangeItem,
        ContextCompactionItem,
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
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    turns: list[Turn] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def last_user_message_preview(self, max_len: int = 80) -> str:
        for turn in reversed(self.turns):
            for item in reversed(turn.items):
                if item.type == "userMessage":
                    text = item.text.replace("\n", " ")
                    if len(text) > max_len:
                        return text[: max_len - 3] + "..."
                    return text
        return "(no messages)"


def parse_item(data: dict[str, Any]) -> Item:
    item_type = data.get("type")
    mapping = {
        "userMessage": UserMessageItem,
        "agentMessage": AgentMessageItem,
        "commandExecution": CommandExecutionItem,
        "fileChange": FileChangeItem,
        "contextCompaction": ContextCompactionItem,
    }
    cls = mapping.get(item_type)
    if cls is None:
        raise ValueError(f"Unknown item type: {item_type}")
    return cls.model_validate(data)
