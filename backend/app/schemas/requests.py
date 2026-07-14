from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WechatLoginIn(BaseModel):
    code: str
    role: str = "parent"
    client_openid: str | None = None


class ProfileUpdateIn(BaseModel):
    nickname: str
    grade: str | None = None
    school: str | None = None


class StudentCreateIn(BaseModel):
    name: str
    grade: str = ""
    school: str | None = None


class FamilyJoinIn(BaseModel):
    invite_code: str
    student_id: int | None = None


class ImportBatchCreateIn(BaseModel):
    student_id: int
    title: str
    period_type: str = "custom"
    start_date: date | None = None
    end_date: date | None = None
    raw_text: str | None = None


class ImportBatchUpdateIn(BaseModel):
    raw_text: str | None = None


class PlanConfirmIn(BaseModel):
    confirmed_item_ids: list[int] = Field(default_factory=list)
    adjustments: list[dict] = Field(default_factory=list)


class StudySessionStartIn(BaseModel):
    daily_task_id: int


class StudySessionFinishIn(BaseModel):
    finish_reason: str = "submit_now"


class SubmissionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_task_id: int
    submission_type: str = "photo"
    linked_study_session_id: int | None = None
    student_note: str | None = None


class CorrectionReviewIn(BaseModel):
    action: Literal["confirm", "resubmit"]
    note: str | None = None
