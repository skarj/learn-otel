from otel_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"
    nats_url: str


settings = Settings()
