from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response
from pydantic import Field

from app.api.dependencies import Actor, Database
from app.api.schemas import InputModel
from app.tools.leave import set_leave_balance
from app.tools.leave_workflow import (
    LeaveConflict, cancel_leave, create_leave_request, get_leave, leave_balance_summary,
    list_calendar, list_leaves, preview_leave, reassign_leave, review_leave, set_calendar_day,
)

router = APIRouter(prefix="/leave", tags=["年假与审批"])


class LeaveInput(InputModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_period: Literal["am", "pm"]
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_period: Literal["am", "pm"]
    reason: str = Field(min_length=1, max_length=1000)


class SubmitInput(LeaveInput):
    request_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_calendar_signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewInput(InputModel):
    step_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=1000)


class VersionInput(InputModel):
    version: int = Field(ge=0, le=9223372036854775807, strict=True)


class CalendarInput(InputModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    is_workday: bool = Field(strict=True)
    note: str = Field(default="", max_length=200)


class BalanceInput(InputModel):
    request_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    username: str = Field(min_length=1, max_length=100)
    days: float = Field(ge=0, le=10000, allow_inf_nan=False, strict=True)


@router.get("/balance")
def balance(user: Actor, db: Database) -> dict[str, object]:
    return leave_balance_summary(db, user)


@router.post("/balance", status_code=204)
def maintain_balance(body: BalanceInput, user: Actor, db: Database) -> Response:
    try:
        set_leave_balance(db, user, body.username, body.days, request_key=body.request_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.get("/calendar")
def calendar(user: Actor, db: Database, start_date: str, end_date: str) -> list[dict[str, object]]:
    try:
        return list_calendar(db, user, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/calendar", status_code=204)
def maintain_calendar(body: CalendarInput, user: Actor, db: Database) -> Response:
    try:
        set_calendar_day(db, user, body.date, body.is_workday, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.post("/preview")
def preview(body: LeaveInput, user: Actor, db: Database) -> dict[str, object]:
    try:
        return preview_leave(db, user, body.start_date, body.start_period, body.end_date, body.end_period, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("", status_code=201)
def submit(body: SubmitInput, user: Actor, db: Database) -> dict[str, object]:
    try:
        request_id = create_leave_request(db, user, body.start_date, body.start_period, body.end_date, body.end_period,
                                          body.reason, request_key=body.request_key, expected_calendar_signature=body.expected_calendar_signature)
        return get_leave(db, user, request_id)
    except LeaveConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def requests(user: Actor, db: Database, view: Literal["mine", "inbox", "reviewed", "all"] = "mine",
             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_leaves(db, user, view, limit, offset)


@router.get("/{request_id}")
def detail(user: Actor, db: Database, request_id: int = Path(gt=0, le=9223372036854775807)) -> dict[str, object]:
    try:
        return get_leave(db, user, request_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{request_id}/review")
def review(body: ReviewInput, user: Actor, db: Database, request_id: int = Path(gt=0, le=9223372036854775807)) -> dict[str, object]:
    try:
        return review_leave(db, user, request_id, body.step_id, body.decision, body.comment)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LeaveConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{request_id}/{action}")
def change(body: VersionInput, user: Actor, db: Database, action: Literal["cancel", "reassign"],
           request_id: int = Path(gt=0, le=9223372036854775807)) -> dict[str, object]:
    try:
        if action == "cancel":
            return cancel_leave(db, user, request_id, body.version)
        return reassign_leave(db, user, request_id, body.version)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LeaveConflict as exc:
        raise HTTPException(409, str(exc)) from exc
