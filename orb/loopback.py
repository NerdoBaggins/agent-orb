"""Calls to the orb's own servers on 127.0.0.1. They never go through a proxy the machine is configured with."""
import urllib.request


def urlopen(request: urllib.request.Request | str, timeout: float):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)
