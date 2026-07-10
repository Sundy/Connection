from backend.app.core.config import Settings
from backend.app.services.ai_config import api_key_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.media_processing_service import prepare_audio_url
from backend.app.services.oss_service import build_public_url, oss_is_configured


def test_service_configuration_uses_shared_dashscope_key_when_specific_key_missing():
    settings = Settings(dashscope_api_key="shared-key", llm_api_key="", ocr_api_key="")

    assert api_key_for(settings, "llm") == "shared-key"
    assert api_key_for(settings, "ocr") == "shared-key"
    assert service_is_configured(settings, "llm") is True
    assert service_is_configured(settings, "ocr") is True


def test_service_configuration_treats_placeholder_keys_as_disabled():
    settings = Settings(dashscope_api_key="请替换为你的DashScope API Key")

    assert service_is_configured(settings, "llm") is False
    assert service_is_configured(settings, "ocr") is False


def test_asr_openai_compatible_requires_public_audio_url():
    settings = Settings(dashscope_api_key="shared-key", asr_provider="qwen")

    assert transcribe_audio_url("/tmp/local.wav", settings=settings) == ""


def test_oss_configuration_uses_existing_aliyun_env_names():
    settings = Settings(
        aliyun_access_key_id="id",
        aliyun_access_key_secret="secret",
        aliyun_oss_endpoint="oss-cn-shenzhen.aliyuncs.com",
        aliyun_oss_bucket="aceflow-connection",
    )

    assert oss_is_configured(settings) is True
    assert build_public_url(settings, "connection/test.wav") == "https://aceflow-connection.oss-cn-shenzhen.aliyuncs.com/connection/test.wav"


def test_extract_text_from_plain_document(tmp_path):
    file_path = tmp_path / "homework.txt"
    file_path.write_text("口算100道，背20个英语单词", encoding="utf-8")

    assert extract_text_from_document(str(file_path), "file") == "口算100道，背20个英语单词"


def test_prepare_local_audio_uploads_to_oss(monkeypatch, tmp_path):
    audio_path = tmp_path / "reading.mp3"
    audio_path.write_bytes(b"fake-audio")

    def fake_upload(file_path, object_key=None):
        return f"https://oss.example.com/{object_key}"

    monkeypatch.setattr("backend.app.services.media_processing_service.upload_file_to_oss", fake_upload)

    assert prepare_audio_url(str(audio_path), "audio").endswith("/asr/reading.mp3")
