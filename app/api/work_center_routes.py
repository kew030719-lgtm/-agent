from fastapi import APIRouter, HTTPException, Path, Query, Response

from app.api.dependencies import Actor, Database
from app.tools.notifications import list_notifications, mark_notification_read
from app.tools.work_center import list_todos, work_center_summary

router = APIRouter(tags=["待办与通知"])


@router.get("/work-center/todos")
def todos(user: Actor, db: Database, limit: int = Query(default=50, ge=1, le=100),
          offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_todos(db, user, limit=limit, offset=offset)


@router.get("/work-center/summary")
def summary(user: Actor, db: Database) -> dict[str, int]:
    return work_center_summary(db, user)


@router.get("/notifications")
def notifications(user: Actor, db: Database, unread_only: bool = False,
                  limit: int = Query(default=50, ge=1, le=100),
                  offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_notifications(db, user, unread_only=unread_only, limit=limit, offset=offset)


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(user: Actor, db: Database,
                      notification_id: int = Path(gt=0, le=9223372036854775807)) -> Response:
    try:
        mark_notification_read(db, user, notification_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(status_code=204)
