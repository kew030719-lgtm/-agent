"""Broker 仅携带任务编号；模型只在 Worker 子进程执行任务时加载。"""

import os

from celery import Celery

from app.config import load_config


load_config()
HARD_TIME_LIMIT = max(120, int(os.getenv("INGESTION_TIME_LIMIT_SECONDS", "3600")))
celery_app = Celery(
    "employee_knowledge",
    broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
    include=["app.ingestion.tasks"],
)
celery_app.conf.update(
    task_default_queue=os.getenv("CELERY_QUEUE", "knowledge"),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    task_time_limit=HARD_TIME_LIMIT,
    task_soft_time_limit=HARD_TIME_LIMIT - 30,
    broker_transport_options={"visibility_timeout": HARD_TIME_LIMIT + 300, "socket_connect_timeout": 2, "socket_timeout": 3},
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    task_publish_retry=False,
    timezone="UTC",
    enable_utc=True,
)
