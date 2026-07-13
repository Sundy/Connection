from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Homework Agent API"
    database_url: str = "sqlite:///./backend/dev.db"
    upload_dir: str = "./backend/uploads"
    redis_url: str = "redis://localhost:6379/0"
    async_tasks_eager: bool = True
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_provider: str = "qwen"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "qwen-plus"
    llm_timeout_seconds: int = 60
    llm_temperature: float = 0.2
    ocr_provider: str = "qwen"
    ocr_api_key: str = ""
    ocr_base_url: str = ""
    ocr_model: str = "qwen-vl-ocr"
    ocr_timeout_seconds: int = 120
    ocr_max_pages: int = 20
    vision_provider: str = "qwen"
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = "qwen-vl-plus"
    vision_timeout_seconds: int = 120
    vision_max_images: int = 8
    annotation_confidence_threshold: float = 0.65
    asr_provider: str = "qwen"
    asr_api_key: str = ""
    asr_base_url: str = ""
    asr_model: str = "qwen3-asr-flash"
    asr_timeout_seconds: int = 300
    asr_hotwords: str = "数学,语文,英语,古诗,口算,应用题"
    ffmpeg_path: str = "ffmpeg"
    video_frame_fps: int = 1
    video_max_duration_seconds: int = 300
    video_max_frames: int = 8
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_oss_endpoint: str = ""
    aliyun_oss_bucket: str = ""
    aliyun_oss_public_base_url: str = ""
    aliyun_oss_prefix: str = "connection"
    aliyun_oss_signed_url_expires_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
