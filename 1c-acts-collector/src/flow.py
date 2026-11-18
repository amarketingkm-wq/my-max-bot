from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from . import ocr
from . import windows as w


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


@dataclass
class ActResult:
    act_title: str
    amount: str
    phone: str
    ticket_title: str = ""
    status: str = "ok"
    error: str = ""
    collected_at: str = ""


class PauseController:
    def __init__(self) -> None:
        self.paused = False
        self.stop = False

    def wait_if_paused(self) -> None:
        while self.paused and not self.stop:
            time.sleep(0.2)


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def ensure_output(cfg: dict) -> tuple[Path, Path]:
    csv_path = ROOT / cfg["output_csv"]
    log_path = ROOT / cfg["processed_log"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "collected_at",
                    "act_title",
                    "amount",
                    "phone",
                    "ticket_title",
                    "status",
                    "error",
                ],
            )
            writer.writeheader()
    log_path.touch(exist_ok=True)
    return csv_path, log_path


def already_processed(log_path: Path) -> set[str]:
    return {
        line.strip()
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_result(csv_path: Path, log_path: Path, result: ActResult) -> None:
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "collected_at",
                "act_title",
                "amount",
                "phone",
                "ticket_title",
                "status",
                "error",
            ],
        )
        writer.writerow(
            {
                "collected_at": result.collected_at,
                "act_title": result.act_title,
                "amount": result.amount,
                "phone": result.phone,
                "ticket_title": result.ticket_title,
                "status": result.status,
                "error": result.error,
            }
        )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(result.act_title + "\n")


def region_right_of_label(
    img: Image.Image,
    label: str,
    width: int = 420,
    height: int = 40,
    lang: str = "eng+rus",
) -> Optional[Image.Image]:
    box = ocr.find_label_box(img, label, lang=lang)
    if not box:
        return None
    x, y, ww, hh = box
    rx = x + ww + 8
    ry = max(0, y - 6)
    return img.crop(
        (
            rx,
            ry,
            min(img.width, rx + width),
            min(img.height, ry + max(height, hh + 12)),
        )
    )


def click_field_value(
    hwnd: int,
    img: Image.Image,
    label: str,
    dx: int = 120,
    lang: str = "eng+rus",
) -> bool:
    box = ocr.find_label_box(img, label, lang=lang)
    if not box:
        return False
    x, y, ww, hh = box
    cx = min(img.width - 40, x + ww + dx)
    cy = y + hh // 2
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def row_icons(
    img: Image.Image,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> list[tuple[int, int]]:
    """Центры маленьких кнопок. Рамки полей и длинный текст не считаются кнопками."""
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(img.width, int(x1)), min(img.height, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    rgb = np.array(img.crop((x0, y0, x1, y1)).convert("RGB"))
    ink = rgb.min(axis=2) < 205
    height, width = ink.shape
    # Сплошная рамка поля. Текст её не даёт: у букв нет непрерывной полосы.
    for yy in range(height):
        run = longest = 0
        for on in ink[yy]:
            run = run + 1 if on else 0
            longest = max(longest, run)
        if longest > width * 0.65:
            ink[yy] = False
    for xx in range(width):
        run = longest = 0
        for on in ink[:, xx]:
            run = run + 1 if on else 0
            longest = max(longest, run)
        if longest > height * 0.75:
            ink[:, xx] = False
    seen = np.zeros_like(ink, dtype=bool)
    centers: list[tuple[int, int]] = []
    ys, xs = np.where(ink)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        minx = maxx = sx
        miny = maxy = sy
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            minx, maxx = min(minx, cx), max(maxx, cx)
            miny, maxy = min(miny, cy), max(maxy, cy)
            for ny in (cy - 1, cy, cy + 1):
                if ny < 0 or ny >= height:
                    continue
                for nx in (cx - 1, cx, cx + 1):
                    if nx < 0 or nx >= width or seen[ny, nx] or not ink[ny, nx]:
                        continue
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        bw, bh = maxx - minx + 1, maxy - miny + 1
        if not (6 <= bw <= 32 and 6 <= bh <= 32):
            continue
        if count < 8:
            continue
        centers.append(
            (x0 + (minx + maxx) // 2, y0 + (miny + maxy) // 2, bw, bh)
        )
    centers.sort(key=lambda p: p[0])
    return centers


def click_header_open_button(hwnd: int, img: Image.Image) -> bool:
    """Нижняя из маленьких кнопок у правого края шапки — открытие залогового билета."""
    icons = row_icons(
        img,
        img.width - 140,
        int(img.height * 0.18),
        img.width - 3,
        int(img.height * 0.40),
    )
    icons = [p for p in icons if p[2] >= 8 and p[3] >= 8 and 0.5 <= p[2] / p[3] <= 2.2]
    if not icons:
        return False
    cx, cy = max(icons, key=lambda point: point[1])[:2]
    print(f"Кнопка у правого края: {cx}, {cy}", flush=True)
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def _peach_top(img: Image.Image) -> Optional[tuple[int, int]]:
    """Верх бежевой полосы «Клиент» на залоговом билете."""
    rgb = np.array(img.convert("RGB"))
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    peach = (red > 210) & (green > 185) & (blue > 160) & (blue < 235) & ((red.astype(int) - blue.astype(int)) > 12)
    fill = peach.mean(axis=1)
    rows = np.where(fill > 0.22)[0]
    if len(rows) == 0:
        return None
    top = int(rows[0])
    return top, min(img.height, top + 36)


def click_client_open_button(hwnd: int, img: Image.Image) -> bool:
    """Квадрат открытия сразу за крестиком в поле «Клиент», до «Дата рождения»."""
    band = _peach_top(img)
    if band is None:
        return False
    y0, y1 = band
    icons = row_icons(img, 80, y0, int(img.width * 0.62), y1)
    buttons = [
        p
        for p in icons
        if 6 <= p[2] <= 22 and 6 <= p[3] <= 22 and 0.45 <= p[2] / p[3] <= 1.8 and p[1] < y0 + 28
    ]
    if not buttons:
        return False
    buttons.sort(key=lambda p: p[0])
    # Крестик очистки стоит слева, кнопка открытия — следующий квадрат.
    target = buttons[0]
    for button in buttons[1:]:
        if button[0] - target[0] <= 28:
            target = button
        else:
            break
    cx, cy = target[0], target[1]
    print(f"Кнопка клиента: {cx}, {cy}", flush=True)
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def _saturation(img: Image.Image, x: int, y: int) -> float:
    patch = np.array(
        img.crop((max(0, x - 10), max(0, y - 10), min(img.width, x + 10), min(img.height, y + 10))).convert("RGB")
    ).astype(int)
    if patch.size == 0:
        return 0.0
    return float((patch.max(axis=2) - patch.min(axis=2)).mean())


def click_phone_magnifier(hwnd: int, img: Image.Image) -> Optional[tuple[int, int]]:
    """Лупа справа от скрытого номера. Цветные значки (мессенджер, трубка) пропускаются."""
    height, width = img.height, img.width
    for y0 in range(int(height * 0.42), int(height * 0.82), 14):
        icons = row_icons(img, int(width * 0.28), y0, int(width * 0.72), y0 + 34)
        merged: list[tuple[int, int, int, int]] = []
        for icon in icons:
            if merged and abs(icon[0] - merged[-1][0]) <= 14 and abs(icon[1] - merged[-1][1]) <= 10:
                continue
            merged.append(icon)
        if len(merged) < 3:
            continue
        gray = [icon for icon in merged if _saturation(img, icon[0], icon[1]) < 28]
        if not gray:
            continue
        cx, cy = gray[0][0], gray[0][1]
        print(f"Лупа телефона: {cx}, {cy}", flush=True)
        w.click_window_relative(hwnd, cx, cy, image_size=img.size)
        return cx, cy
    return None


def click_edge_button(hwnd: int, img: Image.Image, y_center: int) -> bool:
    """Квадратная кнопка открытия у правого края строки, не внутри текста поля."""
    icons = row_icons(
        img,
        img.width - 130,
        y_center - 22,
        img.width - 3,
        y_center + 22,
    )
    if not icons:
        return False
    cx, cy = max(icons, key=lambda point: point[0])
    print(f"Кнопка у правого края: {cx}, {cy}", flush=True)
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def click_open_reference_button(
    hwnd: int,
    img: Image.Image,
    label: str,
    lang: str = "eng+rus",
) -> bool:
    """Click the «две клеточки» open button to the right of a reference field."""
    box = ocr.find_label_box(img, label, lang=lang)
    if not box:
        return False
    x, y, ww, hh = box
    band_left = x + ww
    band = img.crop(
        (
            band_left,
            max(0, y - 12),
            min(img.width, x + ww + 750),
            min(img.height, y + hh + 36),
        )
    )
    for tpl_name in (
        "btn_open_client_tight.png",
        "btn_open_client_alt.png",
        "btn_open_client_area.png",
    ):
        tpl_path = TEMPLATES / tpl_name
        if not tpl_path.exists():
            continue
        match = ocr.find_template(band, Image.open(tpl_path), threshold=0.68)
        if match:
            mx, my, mw, mh = match
            cx = band_left + mx + mw // 2
            cy = max(0, y - 12) + my + mh // 2
            w.click_window_relative(hwnd, cx, cy, image_size=img.size)
            return True

    # Heuristic: open-button cluster near right side of field row
    cx = min(img.width - 28, x + ww + 520)
    cy = y + hh // 2
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def click_reveal_phone(
    hwnd: int,
    img: Image.Image,
    phone_label: str,
    lang: str = "eng+rus",
) -> bool:
    box = ocr.find_label_box(img, phone_label, lang=lang)
    tpl = TEMPLATES / "btn_reveal_phone.png"
    search = img
    origin_x = 0
    origin_y = 0
    if box:
        x, y, ww, hh = box
        origin_x = x
        origin_y = max(0, y - 20)
        search = img.crop(
            (
                x,
                origin_y,
                min(img.width, x + 750),
                min(img.height, y + hh + 45),
            )
        )

    if tpl.exists():
        match = ocr.find_template(search, Image.open(tpl), threshold=0.70)
        if match:
            mx, my, mw, mh = match
            cx = origin_x + mx + mw // 2
            cy = origin_y + my + mh // 2
            w.click_window_relative(hwnd, cx, cy, image_size=img.size)
            return True

    if not box:
        return False
    x, y, ww, hh = box
    # First icon after masked value (лупа), typically ~200-260px right of label end
    cx = min(img.width - 20, x + ww + 230)
    cy = y + hh // 2
    w.click_window_relative(hwnd, cx, cy, image_size=img.size)
    return True


def read_heading(img: Image.Image, lang: str) -> str:
    """Имя формы из верхней части окна, когда 1С не меняет заголовок Windows."""
    top = img.crop((0, 0, img.width, min(img.height, 240)))
    text = ocr.read_text(top, lang=lang)
    for line in text.splitlines():
        clean = " ".join(line.split())
        if "акт оценки" in clean.casefold() or "залоговый билет" in clean.casefold():
            return clean[:180]
    return ""


def same_window(win: w.WindowInfo, title: str) -> w.WindowInfo:
    return w.WindowInfo(hwnd=win.hwnd, title=title or win.title, rect=win.rect)


def close_opened_forms(
    forms: list[Optional[w.WindowInfo]],
    main_hwnd: int,
    delay: float,
) -> None:
    for item in forms:
        if item is None:
            continue
        if item.hwnd == main_hwnd:
            w.close_window(item.hwnd)
            time.sleep(delay)
        else:
            safe_close(item, delay=delay)


def read_amount_from_act(img: Image.Image, sum_label: str, lang: str) -> str:
    upper = img.crop((0, 0, img.width, max(80, int(img.height * 0.48))))
    box = ocr.find_label_box(upper, sum_label, lang=lang, scale=1.0)
    if box is None:
        return ""
    x, y, ww, hh = box
    region = img.crop(
        (
            x + ww,
            max(0, y - 6),
            min(img.width, x + ww + 220),
            min(img.height, y + max(hh, 16) + 8),
        )
    )
    return ocr.extract_sum(region, lang=lang) or ""


def snapshot_hwnds() -> set[int]:
    return {win.hwnd for win in w._enum_windows()}


def wait_new_window(
    before: set[int],
    timeout: float = 10.0,
    exclude_substrings: Optional[list[str]] = None,
) -> Optional[w.WindowInfo]:
    exclude_substrings = [s.lower() for s in (exclude_substrings or [])]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win in w._enum_windows():
            if win.hwnd in before:
                continue
            title_l = win.title.lower()
            if any(s in title_l for s in exclude_substrings):
                continue
            if win.width < 200 or win.height < 150:
                continue
            return win
        time.sleep(0.25)
    return None


def safe_close(*wins: Optional[w.WindowInfo], delay: float = 0.5) -> None:
    for item in wins:
        if item is None:
            continue
        try:
            if not w.wait_until_closed(item.hwnd, timeout=0.1):
                w.close_window(item.hwnd)
                time.sleep(delay)
            if not w.wait_until_closed(item.hwnd, timeout=1.5):
                w.press_escape(item.hwnd)
                time.sleep(delay)
        except Exception:
            pass


def process_one_act(
    cfg: dict,
    list_win: w.WindowInfo,
    pause: PauseController,
    done: Optional[set[str]] = None,
) -> ActResult:
    delays = cfg["delays"]
    lang = cfg["ocr"].get("lang", "eng+rus")
    act_prefix = cfg["act_title_prefix"]
    ticket_prefix = cfg["ticket_title_prefix"]
    list_sub = cfg["list_window_title_substr"]

    pause.wait_if_paused()
    if pause.stop:
        return ActResult("", "", "", status="stopped", collected_at=_now())

    list_name = ensure_act_row(list_win, lang)
    if not list_name:
        return ActResult(
            act_title="",
            amount="",
            phone="",
            status="error",
            error="В списке нет строки, которая начинается с «Акт».",
            collected_at=_now(),
        )
    if done and list_name in done:
        return ActResult(list_name, "", "", status="skip", collected_at=_now())
    print(f"Беру номер для: {list_name}", flush=True)

    before_act = snapshot_hwnds()
    w.brief_focus(list_win.hwnd)
    time.sleep(0.1)
    w.press_enter(list_win.hwnd)

    act = w.wait_for_window(act_prefix, timeout=2.5)
    if not act:
        opened = wait_new_window(before_act, timeout=1.5, exclude_substrings=[list_sub])
        if opened and act_prefix.lower() in opened.title.lower():
            act = opened
    act_img = None
    if act is None:
        time.sleep(delays["after_open"])
        act_img = w.screenshot_window(list_win.hwnd)
        if ocr.find_label_box(act_img, cfg["sum_label"], lang=lang):
            act = same_window(list_win, read_heading(act_img, lang) or "Акт оценки")
    if act is None:
        return ActResult(
            act_title=list_name or "(не открылся)",
            amount="",
            phone="",
            status="error",
            error="Не удалось открыть Акт оценки. Курсор должен стоять на строке акта.",
            collected_at=_now(),
        )

    time.sleep(0.2 if act.hwnd == list_win.hwnd else delays["after_open"])
    pause.wait_if_paused()

    if act_img is None or act.hwnd != list_win.hwnd:
        act_img = w.screenshot_window(act.hwnd)
    if act_prefix.lower() not in act.title.lower():
        heading = read_heading(act_img, lang)
        if heading:
            act = same_window(act, heading)
    print(f"Акт открыт: {act.title}. Жму кнопку залогового билета.", flush=True)
    clicked = click_header_open_button(act.hwnd, act_img)
    if not clicked:
        upper_h = max(80, int(act_img.height * 0.48))
        upper = act_img.crop((0, 0, act_img.width, upper_h))
        words = ocr.word_boxes(upper, lang=lang, scale=1.0)
        ticket_box = ocr.box_for_label(words, cfg["ticket_field_label"])
        if ticket_box:
            _, y, _, hh = ticket_box
            clicked = click_edge_button(act.hwnd, act_img, y + max(hh, 12) // 2)
    if not clicked:
        w.open_reference(act.hwnd)
    ticket = w.wait_for_window(ticket_prefix, timeout=4)
    amount = read_amount_from_act(act_img, cfg["sum_label"], lang) if ticket else ""
    print(f"Сумма: {amount or '-'}", flush=True)
    if not ticket:
        save_debug(act_img, "act_no_ticket")
        close_opened_forms([act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=list_name,
            amount=amount,
            phone="",
            status="error",
            error="Не удалось открыть Залоговый билет",
            collected_at=_now(),
        )

    time.sleep(delays["after_open"])
    pause.wait_if_paused()

    print(f"Залоговый билет: {ticket.title}. Жму кнопку у клиента", flush=True)
    ticket_img = w.screenshot_window(ticket.hwnd)
    before_client = snapshot_hwnds()
    if not click_client_open_button(ticket.hwnd, ticket_img):
        w.open_reference(ticket.hwnd)
    client = w.wait_for_window("Карточка", timeout=4)
    if not client:
        client = wait_new_window(
            before_client,
            timeout=2,
            exclude_substrings=[list_sub, act_prefix, ticket_prefix],
        )
    if not client:
        save_debug(ticket_img, "ticket_no_client")
        close_opened_forms([ticket, act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=list_name,
            amount=amount,
            phone="",
            ticket_title=ticket.title,
            status="error",
            error="Не удалось открыть карточку клиента",
            collected_at=_now(),
        )

    time.sleep(delays["after_open"])
    pause.wait_if_paused()

    print(f"Карточка: {client.title}. Жму лупу у телефона", flush=True)
    client_img = w.screenshot_window(client.hwnd)
    eye = click_phone_magnifier(client.hwnd, client_img)
    if eye is None and not click_reveal_phone(client.hwnd, client_img, cfg["phone_label"], lang=lang):
        save_debug(client_img, "client_no_reveal")
        close_opened_forms([client, ticket, act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=list_name,
            amount=amount,
            phone="",
            ticket_title=ticket.title,
            status="error",
            error="Не найдена кнопка показа телефона",
            collected_at=_now(),
        )

    time.sleep(delays["after_reveal"])
    client_img2 = w.screenshot_window(client.hwnd)
    phone = ""
    if eye is not None:
        shown = client_img2.crop(
            (
                max(0, eye[0] - 320),
                max(0, eye[1] - 22),
                max(0, eye[0] - 4),
                min(client_img2.height, eye[1] + 22),
            )
        )
        phone = ocr.extract_phone(shown, lang=lang) or ""
    if not phone:
        region = region_right_of_label(
            client_img2, cfg["phone_label"], width=300, height=42, lang=lang
        )
        if region is not None:
            phone = ocr.extract_phone(region, lang=lang) or ""
    if not phone:
        save_debug(client_img2, "phone_ocr_fail")

    close_opened_forms([client, ticket, act], list_win.hwnd, delays["after_close"])

    status = "ok" if phone else "partial"
    err = "" if phone else "Сумма собрана, телефон не распознан OCR"
    return ActResult(
        act_title=list_name,
        amount=amount,
        phone=phone or "",
        ticket_title=ticket.title,
        status=status,
        error=err,
        collected_at=_now(),
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_debug(img: Image.Image, name: str) -> None:
    folder = ROOT / "output" / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    img.save(folder / f"{stamp}_{name}.png")


def act_name_from_text(text: str) -> str:
    """Название акта — фрагмент, который начинается со слова «акт»."""
    for chunk in re.split(r"[\t\r\n]+", text or ""):
        clean = " ".join(chunk.split())
        if clean.casefold().startswith("акт"):
            return clean[:180]
    return ""


def selection_band(img: Image.Image) -> Optional[tuple[int, int]]:
    """Жёлтая рамка текущей строки табличного документа: верх и низ."""
    rgb = np.array(img.convert("RGB"))
    red = rgb[:, :, 0].astype(int)
    green = rgb[:, :, 1].astype(int)
    blue = rgb[:, :, 2].astype(int)
    yellow = (red > 230) & (green > 170) & (blue < 140) & (red > blue + 70)
    lines: list[int] = []
    width = yellow.shape[1]
    for y, row in enumerate(yellow):
        run = longest = 0
        for on in row:
            run = run + 1 if on else 0
            if run > longest:
                longest = run
        if longest > width * 0.35:
            lines.append(y)
    if len(lines) < 2:
        return None
    clusters: list[tuple[int, int]] = []
    start = prev = lines[0]
    for y in lines[1:]:
        if y - prev > 2:
            clusters.append((start, prev))
            start = y
        prev = y
    clusters.append((start, prev))
    for index in range(len(clusters) - 1):
        top = clusters[index]
        bottom = clusters[index + 1]
        gap = bottom[0] - top[1]
        if 8 <= gap <= 48:
            return top[1] + 1, bottom[0]
    return None


def row_act_name(list_win: w.WindowInfo, lang: str) -> str:
    """Название из текущей строки списка: сначала копирование ячейки, затем жёлтая рамка."""
    copied = act_name_from_text(w.copy_active_cell(list_win.hwnd))
    if copied:
        return copied
    img = w.screenshot_window(list_win.hwnd)
    band = selection_band(img)
    if band is None:
        return ""
    y0, y1 = band
    strip = img.crop((20, y0, max(21, int(img.width * 0.55)), max(y0 + 1, y1)))
    return act_name_from_text(ocr.read_text(strip, lang=lang))


def ensure_act_row(list_win: w.WindowInfo, lang: str) -> str:
    name = row_act_name(list_win, lang)
    if name:
        print(f"Акт в списке: {name}", flush=True)
        return name
    print("Строка не начинается с «Акт». Спускаюсь вниз.", flush=True)
    return advance_to_next_act(list_win, lang, previous="")


def advance_to_next_act(list_win: w.WindowInfo, lang: str, previous: str) -> str:
    """Вниз, пока текущая строка снова не начнётся со слова «Акт»."""
    print("Ищу строку, которая начинается с «Акт».", flush=True)
    stuck = 0
    last = previous
    for _ in range(30):
        w.brief_focus(list_win.hwnd)
        w.press_down(list_win.hwnd)
        time.sleep(0.25)
        name = row_act_name(list_win, lang)
        if name and name != previous:
            print(f"Следующий акт: {name}", flush=True)
            return name
        if not name:
            stuck = 0
            last = ""
            continue
        if name == last:
            stuck += 1
            if stuck >= 2:
                print("Строки актов закончились.", flush=True)
                return ""
        else:
            stuck = 0
        last = name
    print("Не нашёл следующую строку, которая начинается с «Акт».", flush=True)
    return ""


def run(cfg: dict, pause: PauseController) -> None:
    ocr.configure_tesseract(cfg.get("ocr", {}).get("tesseract_cmd") or "")
    csv_path, log_path = ensure_output(cfg)
    done = already_processed(log_path)

    list_win = w.find_1c_window(cfg.get("list_window_title_substr") or "")
    if not list_win:
        raise RuntimeError(
            "Не вижу окно 1С. Отчёт должен быть открыт и не свёрнут.\n"
            "Окна, которые видит программа:\n"
            + w.describe_windows()
        )

    print(f"Список: {list_win.title}")
    print("F8 — пауза/продолжить, F9 — стоп")
    print(
        "Чтобы работать в других программах: держите 1С открытой и видимой "
        "на втором мониторе (не сворачивайте)."
    )

    max_acts = int(cfg.get("max_acts") or 0)
    processed = 0
    empty_streak = 0

    while not pause.stop:
        pause.wait_if_paused()
        if pause.stop:
            break

        result = process_one_act(cfg, list_win, pause, done)
        if pause.stop or result.status == "stopped":
            break
        lang = cfg["ocr"].get("lang", "eng+rus")

        if result.status == "skip":
            print(f"Пропуск (уже было): {result.act_title}")
        elif result.act_title and result.act_title != "(не открылся)":
            append_result(csv_path, log_path, result)
            done.add(result.act_title)
            processed += 1
            empty_streak = 0 if result.status in ("ok", "partial") else empty_streak + 1
            print(
                f"[{processed}] {result.status}: {result.act_title} | "
                f"сумма={result.amount or '-'} | тел={result.phone or '-'}"
            )
            if result.error:
                print(f"  ! {result.error}")
        else:
            empty_streak += 1
            print(f"Ошибка: {result.error}")

        if empty_streak >= 5:
            print("Слишком много ошибок подряд — остановка.")
            break

        if max_acts and processed >= max_acts:
            print(f"Достигнут лимит max_acts={max_acts}")
            break

        if not result.act_title and result.status == "error":
            break
        if not advance_to_next_act(list_win, lang, result.act_title):
            break
        time.sleep(cfg["delays"]["between_acts"])

    print(f"Готово. CSV: {csv_path}")
