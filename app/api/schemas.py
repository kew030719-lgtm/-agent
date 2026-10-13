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


JobLevel = Literal["employee", "team_lead", "department_manager", "general_manager", "chairman"]
AccessRole = Literal["employee", "hr", "it", "finance", "admin"]


class OrganizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(default="", max_length=100)
    department_id: int | None = Field(default=None, gt=0)
    job_level: JobLevel = "employee"
    manager_username: str | None = Field(default=None, min_length=1, max_length=100)


class OrganizationProfile(BaseModel):
    display_name: str = ""
    department_id: int | None = None
    department_name: str | None = None
    job_level: JobLevel = "employee"
    manager_username: str | None = None


class Profile(OrganizationProfile):
    username: str
    role: str
    permissions: list[str]


class UserCreate(OrganizationInput):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=256)
    role: AccessRole = "employee"

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("账号不能为空")
        return value


class UserUpdate(InputModel):
    display_name: str = Field(max_length=100)
    department_id: int | None = Field(gt=0)
    job_level: JobLevel
    manager_username: str | None = Field(min_length=1, max_length=100)
    role: AccessRole


class DepartmentInput(InputModel):
    name: str = Field(min_length=1, max_length=50)


class UserSummary(OrganizationProfile):
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
    access_mode: str = "legacy"
    can_read: bool = True
    can_manage: bool = False
    id: int
    title: str
    scope: str
    source_format: str | None
    chunks: int
    created_at: str
