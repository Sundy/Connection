from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StudentOut(ORMModel):
    id: int
    name: str
    grade: str
    school: str | None = None


class TaskOut(ORMModel):
    id: int
    subject: str
    title: str
    task_type: str
    submit_type: str
    estimated_minutes: int
    status: str
    task_date: date


class FileOut(ORMModel):
    id: int
    file_name: str
    file_type: str
    file_url: str
    parse_status: str
    sort_order: int


class SessionOut(ORMModel):
    id: int
    daily_task_id: int
    start_time: datetime
    end_time: datetime | None
    duration_seconds: int
    status: str
