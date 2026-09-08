from otel_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "kitchen-service"
    nats_url: str
    order_service_url: str


settings = Settings()
