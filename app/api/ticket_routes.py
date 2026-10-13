from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import Field

from app.api.dependencies import Actor, Database
from app.api.schemas import InputModel
from app.tools.ticket_workflow import TicketConflict, TicketView, get_ticket, list_tickets, transition_ticket

router = APIRouter(prefix="/tickets", tags=["IT 工单"])


class TicketInput(InputModel):
    version: int = Field(ge=0, le=9223372036854775807)
    comment: str = Field(default="", max_length=2000)


@router.get("")
def tickets(user: Actor, db: Database, view: TicketView = "mine",
            limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_tickets(db, user, view, limit, offset)


@router.get("/{ticket_id}")
def detail(user: Actor, db: Database, ticket_id: int = Path(gt=0, le=9223372036854775807)) -> dict[str, object]:
    try:
        return get_ticket(db, user, ticket_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{ticket_id}/{action}")
def transition(body: TicketInput, user: Actor, db: Database,
               action: Literal["claim", "resolve", "confirm", "reopen"],
               ticket_id: int = Path(gt=0, le=9223372036854775807)) -> dict[str, object]:
    try:
        return transition_ticket(db, user, ticket_id, action, body.version, body.comment)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except TicketConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
