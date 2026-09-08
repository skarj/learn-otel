from otel_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "frontend-gateway"
    catalog_service_url: str
    order_service_url: str


settings = Settings()
