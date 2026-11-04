import pytest
from playwright._impl._browser_type import BrowserType

pytest_plugins = ["pytester"]

STARTS_A_BROWSER = ("launch", "launch_persistent_context", "connect", "connect_over_cdp")
running = {}


def refused_unless_marked(name):
    start = getattr(BrowserType, name)

    async def guarded(self, *args, **kwargs):
        item = running.get("item")
        if item is not None and item.get_closest_marker("browser") is None:
            pytest.fail(f"{item.nodeid} started a browser without the browser marker: give its module pytestmark = pytest.mark.browser")
        running["started"] = True
        return await start(self, *args, **kwargs)

    return guarded


for name in STARTS_A_BROWSER:
    setattr(BrowserType, name, refused_unless_marked(name))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item):
    running.update(item=item, started=False)
    try:
        return (yield)
    finally:
        running.clear()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    if item.get_closest_marker("browser") is not None and not running.get("started"):
        pytest.fail(f"{item.nodeid} has the browser marker but started no browser: move it to a module without the marker")
    return result
