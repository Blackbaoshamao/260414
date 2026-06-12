"""Launch AiszrApp, navigate to each page, save a screenshot.

Run from inside d:/Pjt/260414/Aiszr with the project venv.
The worker is created but its run() is never executed -- we don't need real
data, just the UI layout.
"""
import os
import sys
import time

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

# Output directory for screenshots
OUT_DIR = r"d:\Pjt\260414\thesis_figs"
os.makedirs(OUT_DIR, exist_ok=True)

from PyQt5.QtCore import Qt, QTimer, QThread
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication

from ui import AiszrApp, CaptureWorker


# Pages to capture: (index, filename, hint)
TARGETS = [
    (0, "ui_home.png", "首页"),
    (1, "ui_live.png", "直播间"),
    (2, "ui_ai_config.png", "AI 配置"),
    (3, "ui_voice.png", "AI 语音设置"),
    (4, "ui_obs.png", "OBS 联动"),
    (5, "ui_digital_human.png", "数字人推流"),
    (6, "ui_settings.png", "设置"),
]


def main():
    app = QApplication(sys.argv)

    worker = CaptureWorker()
    # Note: we do NOT moveToThread or start the worker. UI pages only need
    # signal hookups, not real Playwright/asyncio activity.

    window = AiszrApp(worker)
    window.finish_init()
    window.show()

    # Force size for consistent screenshots
    window.resize(1280, 800)

    # Let Qt finish layout
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()

    def grab_all():
        for idx, fname, hint in TARGETS:
            print(f"  -> {hint}")
            try:
                window._set_page(idx)
            except Exception:
                pass
            # In SiliconUI the stacked_container handles real switching
            try:
                window.layerMain().page_view.stacked_container.setCurrentIndex(idx)
            except Exception as e:
                print(f"    setCurrentIndex failed: {e}")

            # Let animations/layout settle
            for _ in range(8):
                app.processEvents()
                time.sleep(0.08)

            out = os.path.join(OUT_DIR, fname)
            pix = window.grab()
            pix.save(out, "PNG")
            print(f"    saved {out}")

        QTimer.singleShot(200, app.quit)

    QTimer.singleShot(1500, grab_all)
    app.exec_()


if __name__ == "__main__":
    main()
