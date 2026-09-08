from otel_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "catalog-service"
    postgres_dsn: str
    redis_url: str


settings = Settings()
