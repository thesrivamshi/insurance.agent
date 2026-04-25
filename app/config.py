from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Health Insurance Chatbot"
    log_level: str = "INFO"

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_vector_store_id: str | None = Field(default=None, alias="OPENAI_VECTOR_STORE_ID")
    openai_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_MODEL")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/insurance_chatbot.db",
        alias="DATABASE_URL",
    )
    cors_origins: str = Field(
        default="http://127.0.0.1:3000,http://localhost:3000",
        alias="CORS_ORIGINS",
    )
    chatkit_domain_key: str = Field(default="local-dev", alias="CHATKIT_DOMAIN_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def has_openai_config(self) -> bool:
        return bool(self.openai_api_key and self.openai_vector_store_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
