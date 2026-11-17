from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr

class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://careercoach:change-me@localhost:5432/careercoach"
    frontend_origins: str = "http://localhost:5500,http://127.0.0.1:5500"
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_model_id: str = "gemini-3.8-flash"
    groq_model_id: str = "openai/gpt-oss-120b"
    llm_provider_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_roadmap_max_output_tokens: int = Field(default=4096, ge=256, le=8192)
    upstash_redis_rest_url: SecretStr | None = None
    upstash_redis_rest_token: SecretStr | None = None
    rate_limit_hmac_secret: SecretStr | None = None
    refresh_recovery_encryption_key: SecretStr | None = None
    refresh_recovery_encryption_key_id: str = "primary"
    refresh_recovery_previous_encryption_key: SecretStr | None = None
    refresh_recovery_previous_encryption_key_id: str | None = None
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

settings = Settings()
