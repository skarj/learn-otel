from app_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "order-service"
    postgres_dsn: str
    nats_url: str
    pricing_service_url: str


settings = Settings()
