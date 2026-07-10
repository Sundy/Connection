from backend.app.core.config import Settings
from backend.app.services.planning_service import extract_items


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
