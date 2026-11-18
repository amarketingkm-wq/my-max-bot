#!/usr/bin/env python3
"""
Калибровка кликов по открытому окну 1С.

1. Откройте нужную форму (акт / залоговый / карточка).
2. Запустите: python calibrate_click.py "Залоговый билет"
3. Наведите мышь на кнопку и нажмите F7 — координаты относительно окна сохранятся.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "calibration.json"


def main() -> int:
    if sys.platform != "win32":
        print("Только Windows.")
        return 1

    import keyboard
    import win32api
    import win32gui

    from src import windows as w

    substr = sys.argv[1] if len(sys.argv) > 1 else "Залоговый билет"
    win = w.find_window(substr)
    if not win:
        print(f"Окно не найдено: {substr}")
        return 1

    print(f"Окно: {win.title}")
    print("Наведите курсор на кнопку и нажмите F7. Esc — выход.")
    points = []

    def capture():
        x, y = win32api.GetCursorPos()
        left, top, _, _ = win32gui.GetWindowRect(win.hwnd)
        rel = (x - left, y - top)
        points.append({"screen": [x, y], "relative": list(rel)})
        print(f"Точка #{len(points)}: screen={x},{y} relative={rel[0]},{rel[1]}")

    keyboard.add_hotkey("f7", capture)
    try:
        while True:
            if keyboard.is_pressed("esc"):
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"window": win.title, "points": points}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
