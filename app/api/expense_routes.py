from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from app.api.dependencies import Actor, Database
from app.api.schemas import InputModel
from app.tools.expense_approval import (
    ApprovalConflict, ExpenseView, get_expense, list_expenses, reassign_expense, review_expense,
)

router = APIRouter(prefix="/expenses", tags=["报销与审批"])


class ReviewInput(InputModel):
    step_id: int = Field(gt=0)
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=1000)


@router.get("")
def expenses(user: Actor, db: Database, view: ExpenseView = "mine",
             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_expenses(db, user, view, limit, offset)


@router.get("/{expense_id}")
def detail(expense_id: int, user: Actor, db: Database) -> dict[str, object]:
    try:
        return get_expense(db, user, expense_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{expense_id}/review")
def review(expense_id: int, body: ReviewInput, user: Actor, db: Database) -> dict[str, object]:
    try:
        return review_expense(db, user, expense_id, body.step_id, body.decision, body.comment)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{expense_id}/reassign")
def reassign(expense_id: int, user: Actor, db: Database) -> dict[str, object]:
    try:
        return reassign_expense(db, user, expense_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
