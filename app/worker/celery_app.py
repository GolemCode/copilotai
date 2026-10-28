from celery import Celery

from app.core.settings import REDIS_URL

# Initialisation de l'application Celery avec inclusion explicite
celery_app = Celery(
    "quant_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['app.worker.tasks']  # <- L'instruction de liaison critique
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)