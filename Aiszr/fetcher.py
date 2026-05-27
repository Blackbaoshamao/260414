"""Aiszr: Persistent browser session management and login.

Uses Playwright's launch_persistent_context to maintain browser state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       
(cookies, localStorage, etc.) across restarts without manual cookie files.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext
from loguru import logger

from app_paths import app_dir

USER_DATA_DIR = app_dir() / "browser_data"  
DOUYIN_HOME = "https://www.douyin.com"
PASSPORT_URLS = ("passport.douyin.com", "sso.douyin.com")
_QT_WARNING_FILTER_INSTALLED = False


def _install_qt_warning_filter() -> None:
    """Suppress known noisy Qt/Windows layered-window warnings.

    `UpdateLayeredWindowIndirect failed ... (参数错误)` is a frequent
    Windows-side rendering warning from Qt layered windows/effects and does
    not affect Aiszr's core capture/AI/OBS logic. We filter only this line to
    keep runtime logs readable while preserving other Qt warnings.
    """
    global _QT_WARNING_FILTER_INSTALLED
    if _QT_WARNING_FILTER_INSTALLED:
        return

    from PyQt5.QtCore import QLoggingCategory, qInstallMessageHandler

    # Silence noisy Windows QPA warnings at category level first.
    # Keep any user-provided QT_LOGGING_RULES and only append our rule.
    existing_rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    noise_rule = "qt.qpa.windows.warning=false"
    if noise_rule not in existing_rules:
        os.environ["QT_LOGGING_RULES"] = (
            f"{existing_rules};{noise_rule}" if existing_rules else noise_rule
        )
    try:
        QLoggingCategory.setFilterRules(os.environ["QT_LOGGING_RULES"])
    except Exception:
        # Some runtimes may reject rules from env at this point; fallback to handler.
        pass

    previous_handler = None
    noisy_markers = (
        "UpdateLayeredWindowIndirect failed",
    )

    def _qt_message_handler(msg_type, context, message):
        text = str(message)
        if any(marker in text for marker in noisy_markers):
            return
        if previous_handler is not None:
            previous_handler(msg_type, context, message)
        else:
            sys.stderr.write(text + "\n")

    previous_handler = qInstallMessageHandler(_qt_message_handler)
    _QT_WARNING_FILTER_INSTALLED = True


def clear_session() -> None:
    """Delete browser data directory for a fresh login next time.

    On Windows, browser processes may hold file handles for a brief moment
    after they are closed.  We retry up to 5 times with a short sleep.
    """
    if not USER_DATA_DIR.exists():
        return
    for attempt in range(5):
        try:
            shutil.rmtree(USER_DATA_DIR)
            logger.info("Browser data cleared: {}", USER_DATA_DIR)
            return
        except OSError:
            if attempt < 4:
                import time
                time.sleep(0.3)
    logger.warning("Could not fully delete browser data (files may be locked)")


async def launch_context(headless=False, start_minimized=False):
    """Launch browser with persistent context.

    Browser state (cookies, localStorage, sessionStorage) persists in
    USER_DATA_DIR across restarts — no manual cookie file management.

    Tries browsers in order: Edge (always on Windows) → Chrome → Playwright Chromium.

    Args:
        headless: If True, run in headless mode.
        start_minimized: If True, launch minimized to taskbar.

    Returns:
        tuple: (playwright, context) -- caller is responsible for cleanup.
    """
    pw = await async_playwright().start()
    args = ["--disable-blink-features=AutomationControlled"]
    if start_minimized:
        args.append("--start-minimized")

    for channel in ("msedge", "chrome", None):
        try:
            kwargs = dict(
                user_data_dir=str(USER_DATA_DIR.resolve()),
                headless=headless,
                args=args,
                viewport={"width": 1920, "height": 1080},
            )
            if channel:
                kwargs["channel"] = channel
            context = await pw.chromium.launch_persistent_context(**kwargs)
            name = channel or "chromium"
            mode = "headless" if headless else "headed"
            logger.info("Browser launched: {} ({})", name, mode)
            return pw, context
        except Exception as e:
            logger.debug("Browser {} unavailable: {}", channel or "chromium", e)

    raise RuntimeError("没有可用的浏览器，请安装 Edge 或 Chrome，或运行: playwright install chromium")


async def is_logged_in(context: BrowserContext) -> bool:
    """Validate login state by navigating to Douyin and checking cookies.

    Opens a new page, navigates to douyin.com, waits for network idle,
    then checks for sessionid cookie. Douyin homepage is accessible without
    login, so URL check alone is insufficient.

    Args:
        context: Playwright BrowserContext with cookies loaded.

    Returns:
        True if the session is valid, False if expired or on error.
    """
    page = await context.new_page()
    try:
        await page.goto(DOUYIN_HOME, wait_until="domcontentloaded", timeout=30000)
        current_url = page.url

        # If redirected to passport/sso, login is expired
        if any(passport in current_url for passport in PASSPORT_URLS):
            logger.warning("Login expired -- redirected to {}", current_url)
            return False

        # Douyin homepage is accessible without login, check session cookie
        cookies = await context.cookies()
        has_session = any(c["name"] == "sessionid" for c in cookies)
        if not has_session:
            logger.warning("No sessionid cookie found -- not logged in")
            return False

        logger.info("Login valid -- on {}", current_url)
        return True

    except Exception as e:
        logger.warning("Login check failed: {}", e)
        return False

    finally:
        await page.close()


async def startup():
    """Launch browser with persistent context and ensure valid login.

    Browser state (cookies, localStorage) persists in USER_DATA_DIR.
    If no valid session exists, opens Douyin homepage for QR scan login.
    Waits up to 2.5 minutes for the user to scan.

    Returns:
        tuple: (playwright, context) ready for capture.
    """
    pw, context = await launch_context(start_minimized=True)

    if await is_logged_in(context):
        logger.info("Persistent session valid, ready for capture")
        return pw, context

    # Not logged in — relaunch in normal mode for QR scan
    logger.info("No valid session, relaunching in normal mode for QR scan...")
    await context.close()
    await pw.stop()

    pw, context = await launch_context()
    page = await context.new_page()
    await page.goto(DOUYIN_HOME, wait_until="domcontentloaded", timeout=60000)

    for _ in range(150):  # up to 2.5 minutes
        cookies = await context.cookies()
        if any(c["name"] == "sessionid" for c in cookies):
            logger.info("Login detected, settling cookies...")
            await asyncio.sleep(3)
            logger.info("Session ready ({} cookies)", len(await context.cookies()))
            break
        await asyncio.sleep(1)
    else:
        logger.warning("Timed out waiting for login")

    return pw, context


def _run_desktop():
    """Launch Aiszr desktop application (PyQt5 + SiliconUI)."""
    _install_qt_warning_filter()

    from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
    from PyQt5.QtCore import QThread, Qt, QTimer, QPoint, QTranslator, QLibraryInfo, QLocale
    from PyQt5.QtGui import QPixmap, QPainter, QFont, QColor
    from ui import AiszrApp, CaptureWorker

    # Enable HighDPI BEFORE QApplication is created — required for crisp text rendering.
    # Without these flags, Qt renders text at logical pixel size then upscales (jaggy).
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        pass  # Older PyQt5 versions lack this API

    app = QApplication(sys.argv)
    app.setApplicationName("Aiszr")

    # Register bundled Inter TTFs (macOS Sonoma look). Missing fonts/ → no-op,
    # the cascade below falls back to PingFang SC / 阿里巴巴 / YaHei.
    from ui_theme import register_app_fonts
    register_app_fonts()

    # Load Qt's built-in Simplified Chinese translation so standard widget
    # context menus (Cut/Copy/Paste/Select All on QLineEdit/QTextEdit etc.)
    # show 中文 labels instead of English.
    _qt_translator = QTranslator(app)
    _qm_path = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    if _qt_translator.load("qt_zh_CN", _qm_path):
        app.installTranslator(_qt_translator)
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))

    # Global app font — Inter (Latin) → PingFang SC / 阿里巴巴 / YaHei (CJK).
    # Qt picks per-glyph from the family list so mixed text renders correctly.
    _ui_font = QFont("Inter", 10)
    try:
        _ui_font.setFamilies([
            "Inter",
            "PingFang SC",
            "阿里巴巴普惠体 3.0 55 Regular",
            "Microsoft YaHei UI",
        ])
    except AttributeError:
        pass  # Qt < 5.13 lacks setFamilies; single family + stylesheet cascade still works
    _ui_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    try:
        _ui_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    except AttributeError:
        pass
    app.setFont(_ui_font)

    # Transparent splash window
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    splash = None
    if os.path.exists(icon_path):
        src = QPixmap(icon_path)
        splash_size = min(src.width(), 360)
        scaled = src.scaled(splash_size, splash_size,
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)

        splash = QWidget()
        splash.setAttribute(Qt.WA_TranslucentBackground)
        splash.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        splash.setFixedSize(scaled.size())

        layout = QVBoxLayout(splash)
        layout.setContentsMargins(0, 0, 0, 0)
        img_label = QLabel(splash)
        img_label.setPixmap(scaled)
        layout.addWidget(img_label)

        screen = app.primaryScreen().geometry()
        splash.move(
            (screen.width() - splash.width()) // 2,
            (screen.height() - splash.height()) // 2,
        )
        splash.show()
        app.processEvents()

    worker = CaptureWorker()
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    window = AiszrApp(worker)

    def _finish():
        window.finish_init()
        window.show()
        if splash:
            splash.close()
        app.aboutToQuit.connect(lambda: (worker.stop(), thread.quit(), thread.wait(5000)))
        thread.start()

    QTimer.singleShot(50, _finish)
    sys.exit(app.exec_())


if __name__ == "__main__":
    _run_desktop()

