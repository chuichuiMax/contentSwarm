from fastapi import Request


def request_public_base_url(request: Request) -> str:
    """Return the externally visible origin and optional reverse-proxy prefix."""
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",", 1)[0].strip()
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    ).split(",", 1)[0].strip()
    prefix = request.headers.get("x-forwarded-prefix", "").split(",", 1)[0].strip().rstrip("/")
    if prefix and not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return f"{scheme}://{host}{prefix}"
