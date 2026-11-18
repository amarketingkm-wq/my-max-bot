from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import mss
from PIL import Image

try:
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError as exc:  # pragma: no cover - Linux CI
    raise SystemExit(
        "Этот сборщик рассчитан на Windows. Установите pywin32 и запускайте на ПК с 1С."
    ) from exc


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom

    @property
    def width(self) -> int:
        l, t, r, b = self.rect
        return max(0, r - l)

    @property
    def height(self) -> int:
        l, t, r, b = self.rect
        return max(0, b - t)


def _enum_windows() -> list[WindowInfo]:
    result: list[WindowInfo] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title.strip():
            return
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        result.append(WindowInfo(hwnd=hwnd, title=title, rect=rect))

    win32gui.EnumWindows(callback, None)
    return result


def find_windows(substr: str) -> list[WindowInfo]:
    needle = substr.lower()
    return [w for w in _enum_windows() if needle in w.title.lower()]


def find_window(substr: str) -> Optional[WindowInfo]:
    wins = find_windows(substr)
    if not wins:
        return None
    # prefer largest visible window
    return max(wins, key=lambda w: w.width * w.height)


# В такси-интерфейсе отчёт — вкладка. Заголовок окна Windows при этом
# «… / Управление торговлей … (1С:Предприятие)», а не имя отчёта.
ONE_C_TITLE_HINTS = (
    "1С:Предприятие",
    "1C:Предприятие",
    "1C:Enterprise",
    "Управление торговлей",
)


def find_1c_window(extra: str = "") -> Optional[WindowInfo]:
    needles: list[str] = []
    if extra:
        needles.append(extra)
    needles.extend(ONE_C_TITLE_HINTS)
    seen: set[str] = set()
    for needle in needles:
        key = needle.casefold()
        if not needle or key in seen:
            continue
        seen.add(key)
        win = find_window(needle)
        if win and win.width >= 400 and win.height >= 300:
            return win
    return None


def describe_windows(limit: int = 20) -> str:
    lines: list[str] = []
    for win in _enum_windows():
        if win.width < 200 or win.height < 120:
            continue
        lines.append(f"- {win.title}")
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else "(нет видимых окон)"


def find_window_title_startswith(prefix: str) -> Optional[WindowInfo]:
    needle = prefix.lower()
    wins = [w for w in _enum_windows() if w.title.lower().startswith(needle)]
    if not wins:
        # soft match: contains
        wins = [w for w in _enum_windows() if needle in w.title.lower()]
    if not wins:
        return None
    return max(wins, key=lambda w: w.width * w.height)


def brief_focus(hwnd: int) -> None:
    """Short foreground activation — needed for many 1C managed forms."""
    try:
        # restore if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # AttachThreadInput trick for reliable SetForegroundWindow
        fg = win32gui.GetForegroundWindow()
        cur_thread = win32api.GetCurrentThreadId()
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
        tgt_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        if fg_thread and fg_thread != cur_thread:
            win32process.AttachThreadInput(cur_thread, fg_thread, True)
        if tgt_thread and tgt_thread != cur_thread:
            win32process.AttachThreadInput(cur_thread, tgt_thread, True)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        if fg_thread and fg_thread != cur_thread:
            win32process.AttachThreadInput(cur_thread, fg_thread, False)
        if tgt_thread and tgt_thread != cur_thread:
            win32process.AttachThreadInput(cur_thread, tgt_thread, False)
    except Exception:
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass


def screenshot_window(hwnd: int) -> Image.Image:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    left = max(0, left)
    top = max(0, top)
    width = max(1, right - left)
    height = max(1, bottom - top)
    with mss.mss() as sct:
        grab = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    return win32gui.ScreenToClient(hwnd, (x, y))


def click_screen(x: int, y: int, double: bool = False, hwnd: Optional[int] = None) -> None:
    if hwnd:
        brief_focus(hwnd)
        time.sleep(0.05)
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if double:
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def click_window_relative(
    hwnd: int,
    x: int,
    y: int,
    double: bool = False,
    image_size: Optional[tuple[int, int]] = None,
) -> None:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    if image_size and image_size[0] and image_size[1]:
        win_w = max(1, right - left)
        win_h = max(1, bottom - top)
        if image_size != (win_w, win_h):
            x = int(round(x * win_w / image_size[0]))
            y = int(round(y * win_h / image_size[1]))
    click_screen(left + int(x), top + int(y), double=double, hwnd=hwnd)


def send_keys(hwnd: int, *vk_keys: int) -> None:
    """Send key combination to window via keybd_event after brief focus."""
    brief_focus(hwnd)
    time.sleep(0.05)
    for vk in vk_keys:
        win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    for vk in reversed(vk_keys):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def open_reference(hwnd: int) -> None:
    """1C: Ctrl+Shift+F4 — открыть значение ссылочного поля."""
    send_keys(
        hwnd,
        win32con.VK_CONTROL,
        win32con.VK_SHIFT,
        win32con.VK_F4,
    )


def close_window(hwnd: int) -> None:
    """Ctrl+F4 — закрыть текущее окно формы 1С."""
    send_keys(hwnd, win32con.VK_CONTROL, win32con.VK_F4)


def press_escape(hwnd: int) -> None:
    send_keys(hwnd, win32con.VK_ESCAPE)


def press_down(hwnd: int) -> None:
    send_keys(hwnd, win32con.VK_DOWN)


def press_enter(hwnd: int) -> None:
    send_keys(hwnd, win32con.VK_RETURN)


def wait_for_window(
    prefix: str,
    timeout: float = 8.0,
    poll: float = 0.25,
) -> Optional[WindowInfo]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        win = find_window_title_startswith(prefix)
        if win:
            return win
        time.sleep(poll)
    return None


def wait_until_closed(hwnd: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not win32gui.IsWindow(hwnd):
            return True
        time.sleep(0.2)
    return not win32gui.IsWindow(hwnd)
