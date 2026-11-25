import urllib.request

HOST = "127.0.0.1"


def origin(port: int) -> str:
    return f"http://{HOST}:{port}"


def urlopen(request: urllib.request.Request | str, timeout: float):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)
