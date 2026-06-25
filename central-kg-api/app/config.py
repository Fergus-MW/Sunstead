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

    # OpenSearch BM25 full-text recall (the `/search` endpoint). Optional — when unset, `/search` falls back to
    # the trigram path so nothing breaks. Same index the seed mirror writes (`seed/mirror_opensearch.py`).
    opensearch_url: str | None = None
    opensearch_index: str = "kg-nodes"

    # Embedding column kept in the schema for forward compatibility (Voyage,
    # sentence-transformers, etc.) but no provider is wired up today —
    # Anthropic does not ship an embeddings API.
    embedding_dim: int = 1536

    max_extract_tokens: int = 2048
    default_subgraph_hops: int = 2
    default_query_limit: int = 12


@lru_cache
def get_settings() -> Settings:
    return Settings()
