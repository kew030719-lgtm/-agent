from fastapi import APIRouter, HTTPException, Path, Query

from app.api.dependencies import Actor, Database
from app.ingestion.jobs import JobConflictError as JobConflict, get_job, list_jobs, publish_job, retry_job

router = APIRouter(prefix="/knowledge-jobs", tags=["知识库后台任务"])


@router.get("")
def jobs(user: Actor, db: Database, limit: int = Query(default=20, ge=1, le=100),
         offset: int = Query(default=0, ge=0, le=1000000)) -> list[dict[str, object]]:
    return list_jobs(db, user, limit=limit, offset=offset)


@router.get("/{job_id}")
def detail(user: Actor, db: Database, job_id: str = Path(min_length=1, max_length=64)) -> dict[str, object]:
    try:
        return get_job(db, user, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{job_id}/retry", status_code=202)
def retry(user: Actor, db: Database, job_id: str = Path(min_length=1, max_length=64)) -> dict[str, object]:
    try:
        job = retry_job(db, user, job_id)
        publish_job(db, str(job["id"]))
        return get_job(db, user, str(job["id"]))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
