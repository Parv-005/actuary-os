"""App config (pydantic-settings). Thresholds + env per plan §4/D1."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    api_version: str = "0.1.0"
    app_db_schema: str = "public"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:6543/postgres"
    # Direct (non-pooled) URL for DDL/migrations; falls back to database_url
    direct_database_url: str = ""

    supabase_url: str = "http://localhost:8001"
    supabase_service_key: str = "change-me"
    supabase_bucket: str = "vortex-files"
    # [ID] supabase | local | memory — local/memory for offline dev & tests
    storage_backend: str = "supabase"
    storage_local_root: str = ".data/storage"

    llm_provider: str = "fake"  # openai_compatible | fake
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "change-me"
    llm_model: str = "gpt-4o-mini"
    llm_cost_in_per_mtok: float = 0.15   # $ per 1M input tokens (mini-class)
    llm_cost_out_per_mtok: float = 0.60  # $ per 1M output tokens
    llm_timeout_s: float = 170.0         # agent cap is 180s (§6.6)

    cors_origins: str = "http://localhost:3000"

    # Thresholds (§6.4, §6.6, §13)
    recon_pass_pct: float = 0.5
    recon_warn_pct: float = 2.0
    lr_shift_warn_pp: float = 3.0
    volume_shift_warn_pct: float = 30.0
    assumption_variance_gate_pp: float = 5.0
    fuzzy_map_threshold: float = 0.85
    qa_tolerance_pp: float = 0.05

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
