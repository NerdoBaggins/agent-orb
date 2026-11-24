import urllib.request


def urlopen(request: urllib.request.Request | str, timeout: float):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)
