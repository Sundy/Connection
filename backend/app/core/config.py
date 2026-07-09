from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Homework Agent API"
    database_url: str = "sqlite:///./backend/dev.db"
    upload_dir: str = "./backend/uploads"
    redis_url: str = "redis://localhost:6379/0"
    async_tasks_eager: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
