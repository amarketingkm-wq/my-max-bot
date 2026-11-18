#!/usr/bin/env python3
"""Показать окна 1С — удобно проверить заголовки перед сбором."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    if sys.platform != "win32":
        print("Только Windows.")
        return 1
    from src import windows as w

    wins = sorted(w._enum_windows(), key=lambda x: x.title.lower())
    print(f"Видимых окон: {len(wins)}\n")
    for win in wins:
        mark = ""
        t = win.title.lower()
        if "1с" in t or "1c" in t or "акт" in t or "залогов" in t or "обращения" in t:
            mark = " <<<"
        print(f"[{win.hwnd}] {win.title} ({win.width}x{win.height}){mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
