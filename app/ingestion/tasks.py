"""Celery 消息只能指定已持久化的 job_id，身份与路径均从数据库读取。"""

from celery import Task

from app.ingestion.celery_app import celery_app
from app.ingestion.jobs import BusyJobError
from app.ingestion.worker import execute_job


@celery_app.task(bind=True, name="knowledge.ingest", max_retries=None)
def ingest_document(self: Task, job_id: str) -> None:
    try:
        execute_job(job_id)
    except BusyJobError:
        # 原 worker 若已崩溃，租约到期后该消息接管；正常重复任务最终直接退出。
        raise self.retry(countdown=30, max_retries=None)
