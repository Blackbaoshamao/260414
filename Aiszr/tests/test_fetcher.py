"""Tests for fetcher.py persistent context and login functions."""
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path


class TestLaunchContext:
    """Browser launch with persistent context."""

    async def test_launch_context_headed(self, mock_playwright, mock_context):
        """launch_context() launches persistent context in headed mode."""
        from fetcher import launch_context

        with patch("fetcher.async_playwright", return_value=mock_playwright):
            pw, ctx = await launch_context(headless=False)

        mock_playwright.chromium.launch_persistent_context.assert_called_once()
        call_kwargs = mock_playwright.chromium.launch_persistent_context.call_args[1]
        assert call_kwargs["headless"] is False
        assert call_kwargs["channel"] in ("msedge", "chrome")
        assert ctx is mock_context

    async def test_launch_context_headless(self, mock_playwright, mock_context):
        """launch_context(headless=True) launches in headless mode."""
        from fetcher import launch_context

        with patch("fetcher.async_playwright", return_value=mock_playwright):
            pw, ctx = await launch_context(headless=True)

        call_kwargs = mock_playwright.chromium.launch_persistent_context.call_args[1]
        assert call_kwargs["headless"] is True


class TestClearSession:
    """Session clearing via browser data directory deletion."""

    def test_clear_session_removes_dir(self, tmp_path, monkeypatch):
        """clear_session() removes the browser data directory."""
        from fetcher import clear_session

        # Create a fake browser_data dir
        data_dir = tmp_path / "browser_data"
        data_dir.mkdir()
        (data_dir / "cookies.txt").write_text("fake")

        monkeypatch.setattr("fetcher.USER_DATA_DIR", data_dir)
        clear_session()

        assert not data_dir.exists()

    def test_clear_session_no_dir(self, tmp_path, monkeypatch):
        """clear_session() is safe when no directory exists."""
        from fetcher import clear_session

        data_dir = tmp_path / "nonexistent"
        monkeypatch.setattr("fetcher.USER_DATA_DIR", data_dir)
        clear_session()  # should not raise


class TestIsLoggedIn:
    """Login state validation via URL redirect and cookie check."""

    async def test_is_logged_in_valid(self, mock_context, mock_page):
        """Valid login: page stays on douyin.com, returns True."""
        from fetcher import is_logged_in

        mock_page.url = "https://www.douyin.com/"
        mock_context.new_page = AsyncMock(return_value=mock_page)

        result = await is_logged_in(mock_context)

        assert result is True
        mock_page.close.assert_called_once()

    async def test_is_logged_in_expired_passport(self, mock_context, mock_page):
        """Expired login: redirected to passport.douyin.com, returns False."""
        from fetcher import is_logged_in

        mock_page.url = "https://passport.douyin.com/login"
        mock_context.new_page = AsyncMock(return_value=mock_page)

        result = await is_logged_in(mock_context)

        assert result is False
        mock_page.close.assert_called_once()

    async def test_is_logged_in_expired_sso(self, mock_context, mock_page):
        """Expired login: redirected to sso.douyin.com, returns False."""
        from fetcher import is_logged_in

        mock_page.url = "https://sso.douyin.com/auth"
        mock_context.new_page = AsyncMock(return_value=mock_page)

        result = await is_logged_in(mock_context)

        assert result is False
        mock_page.close.assert_called_once()

    async def test_is_logged_in_exception(self, mock_context, mock_page):
        """Exception during navigation returns False gracefully."""
        from fetcher import is_logged_in

        mock_page.goto = AsyncMock(side_effect=Exception("Network error"))
        mock_context.new_page = AsyncMock(return_value=mock_page)

        result = await is_logged_in(mock_context)

        assert result is False
        mock_page.close.assert_called_once()
