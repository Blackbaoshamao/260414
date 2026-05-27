"""Shared test fixtures for DyStream-Watcher tests."""
import json
import pytest
from unittest.mock import AsyncMock
from pathlib import Path

# Sample cookies matching Playwright's context.cookies() output format
SAMPLE_COOKIES = [
    {
        "name": "sessionid",
        "value": "test123",
        "domain": ".douyin.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    },
    {
        "name": "ttwid",
        "value": "ttwid456",
        "domain": ".douyin.com",
        "path": "/",
        "expires": 1745000000.0,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    },
]


@pytest.fixture
def mock_page():
    """AsyncMock page with default url set to Douyin homepage."""
    page = AsyncMock()
    page.url = "https://www.douyin.com/"
    page.goto = AsyncMock(return_value=AsyncMock())
    page.close = AsyncMock()
    page.wait_for_url = AsyncMock()
    return page


@pytest.fixture
def mock_context(mock_page):
    """AsyncMock browser context with cookie operations."""
    context = AsyncMock()
    context.cookies = AsyncMock(return_value=SAMPLE_COOKIES)
    context.add_cookies = AsyncMock()
    context.new_page = AsyncMock(return_value=mock_page)
    context.close = AsyncMock()
    return context


@pytest.fixture
def mock_playwright(mock_context):
    """AsyncMock Playwright instance with launch_persistent_context."""
    pw = AsyncMock()
    pw.start = AsyncMock(return_value=pw)
    pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)
    pw.stop = AsyncMock()
    return pw
