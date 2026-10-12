from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class ChatInput(InputModel):
    question: str = Field(min_length=1, max_length=2000)
    request_id: UUID


class FeedbackInput(InputModel):
    rating: Literal["有用", "无用"]


class Profile(BaseModel):
    username: str
    role: str
    permissions: list[str]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["employee", "hr", "it", "finance", "admin"] = "employee"

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("账号不能为空")
        return value


class UserSummary(BaseModel):
    username: str
    role: str
    enabled: bool


class RoleOption(BaseModel):
    role: str
    permissions: list[str]


class Conversation(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ChatTurn(BaseModel):
    id: int
    question: str
    answer: str
    domain: str
    status: str
    feedback_allowed: bool
    rating: str | None
    created_at: str


class KnowledgeFile(BaseModel):
    id: int
    title: str
    scope: str
    source_format: str | None
    chunks: int
    created_at: str
