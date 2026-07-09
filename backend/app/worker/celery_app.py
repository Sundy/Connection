from celery import Celery

from backend.app.core.config import settings

celery_app = Celery("homework_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_always_eager = settings.async_tasks_eager
celery_app.conf.task_eager_propagates = True
celery_app.autodiscover_tasks(["backend.app.worker.tasks"])
