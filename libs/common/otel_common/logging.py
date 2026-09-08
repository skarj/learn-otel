import logging
import sys


def configure_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        stream=sys.stdout,
        level=level.upper(),
        format=f"%(asctime)s %(levelname)s [{service_name}] %(name)s: %(message)s",
    )
    return logging.getLogger(service_name)
