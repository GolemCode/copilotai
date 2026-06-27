import os
from celery import Celery

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"

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