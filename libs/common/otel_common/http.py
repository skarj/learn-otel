import httpx


def create_http_client(base_url: str = "", timeout: float = 5.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)
