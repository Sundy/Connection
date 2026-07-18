from celery import Celery
from celery.signals import worker_process_init

from backend.app.core.config import settings
from backend.app.core.database import init_db

celery_app = Celery("homework_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_always_eager = settings.async_tasks_eager
celery_app.conf.task_eager_propagates = True
celery_app.autodiscover_tasks(["backend.app.worker.tasks"])


@worker_process_init.connect
def initialize_worker_database(**_kwargs) -> None:
    init_db()
