from datetime import date

from pydantic import BaseModel, Field


class WechatLoginIn(BaseModel):
    code: str
    role: str = "parent"


class StudentCreateIn(BaseModel):
    name: str
    grade: str = ""
    school: str | None = None


class ImportBatchCreateIn(BaseModel):
    student_id: int
    title: str
    period_type: str = "custom"
    start_date: date | None = None
    end_date: date | None = None
    raw_text: str | None = None


class PlanConfirmIn(BaseModel):
    confirmed_item_ids: list[int] = Field(default_factory=list)
    adjustments: list[dict] = Field(default_factory=list)


class StudySessionStartIn(BaseModel):
    daily_task_id: int


class StudySessionFinishIn(BaseModel):
    finish_reason: str = "submit_now"


class SubmissionCreateIn(BaseModel):
    daily_task_id: int
    submission_type: str = "photo"
    linked_study_session_id: int | None = None
    student_note: str | None = None
