from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Common env-driven settings every service extends with its own fields."""

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    service_name: str
    port: int = 8000
    log_level: str = "INFO"
    otlp_endpoint: str
