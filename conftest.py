import pytest
from playwright.async_api import BrowserType

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def chrome_only_in_browser_tests(request, monkeypatch):
    if request.node.get_closest_marker("browser") is None:
        async def refuse(*args, **kwargs):
            pytest.fail(f"{request.node.nodeid} launched Chrome without the browser marker: give its module pytestmark = pytest.mark.browser")
        monkeypatch.setattr(BrowserType, "launch", refuse)
