from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://kg:kg@localhost:5432/kg",
        description="SQLAlchemy async URL. For Aiven, use postgresql+asyncpg://...?ssl=require",
    )

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    max_extract_tokens: int = 2048
    default_subgraph_hops: int = 2
    default_query_limit: int = 12


@lru_cache
def get_settings() -> Settings:
    return Settings()
