from backend.app.core.config import Settings
from datetime import date, timedelta
from types import SimpleNamespace

from backend.app.services.planning_service import create_daily_tasks, extract_items


class CollectingSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def test_independent_import_files_start_on_plan_start_date():
    db = CollectingSession()
    start = date.today()
    plan = SimpleNamespace(id=10, student_id=20, start_date=start, end_date=start + timedelta(days=6))
    item = SimpleNamespace(
        id=30,
        unit="份",
        total_quantity=1,
        subject="数学",
        title="第二份资料",
        task_type="written",
        submit_type="photo",
        estimated_minutes_total=60,
    )

    create_daily_tasks(db, plan, item, day_offset=3)

    assert db.added[0].task_date == start


def test_extract_items_classifies_subject_from_assignment_content_without_course_input():
    items = extract_items("每天口算100道，背20个英语单词，读课文3遍")

    subjects = {item["subject"] for item in items}

    assert "数学" in subjects
    assert "英语" in subjects
    assert "语文" in subjects


def test_settings_accept_qwen_model_configuration():
    settings = Settings(
        dashscope_api_key="shared-key",
        llm_provider="qwen",
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_model="qwen-plus",
    )

    assert settings.llm_provider == "qwen"
    assert settings.dashscope_api_key == "shared-key"
    assert settings.llm_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.llm_model == "qwen-plus"


def test_settings_cover_ocr_vision_asr_and_video_processing():
    settings = Settings(
        dashscope_api_key="shared-key",
        ocr_provider="qwen",
        ocr_model="qwen-vl-ocr",
        vision_provider="qwen",
        vision_model="qwen-vl-plus",
        asr_provider="qwen",
        asr_model="qwen3-asr-flash",
        ffmpeg_path="/opt/homebrew/bin/ffmpeg",
        video_frame_fps=1,
        video_max_duration_seconds=300,
    )

    assert settings.ocr_model == "qwen-vl-ocr"
    assert settings.vision_model == "qwen-vl-plus"
    assert settings.asr_model == "qwen3-asr-flash"
    assert settings.ffmpeg_path == "/opt/homebrew/bin/ffmpeg"
    assert settings.video_frame_fps == 1
    assert settings.video_max_duration_seconds == 300
