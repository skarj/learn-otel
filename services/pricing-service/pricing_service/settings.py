from otel_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "pricing-service"


settings = Settings()
