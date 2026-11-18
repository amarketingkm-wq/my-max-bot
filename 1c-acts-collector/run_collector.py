#!/usr/bin/env python3
"""Сбор суммы и телефона из актов оценки 1С по открытому окну отчёта."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.flow import PauseController, load_config, run  # noqa: E402


def main() -> int:
    if sys.platform != "win32":
        print("Запускайте этот скрипт на Windows-ПК, где открыта 1С.")
        return 1

    cfg = load_config(ROOT / "config.json")
    pause = PauseController()

    try:
        import keyboard

        def toggle_pause():
            pause.paused = not pause.paused
            print("ПАУЗА" if pause.paused else "ПРОДОЛЖАЕМ")

        def stop():
            pause.stop = True
            pause.paused = False
            print("СТОП")

        keyboard.add_hotkey(cfg["hotkeys"]["pause"], toggle_pause)
        keyboard.add_hotkey(cfg["hotkeys"]["stop"], stop)
    except Exception as exc:
        print(f"Горячие клавиши недоступны ({exc}). Остановка — Ctrl+C.")

    print("Старт через 3 секунды. Курсор в списке — на нужном «Акт оценки …».")
    time.sleep(3)
    try:
        run(cfg, pause)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 0
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
