from __future__ import annotations

import csv
import json
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
    w.click_window_relative(hwnd, cx, cy)
    return True


def row_icons(
    img: Image.Image,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> list[tuple[int, int]]:
    """Центры маленьких кнопок на строке поля. Длинный текст пропускается."""
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(img.width, int(x1)), min(img.height, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    rgb = np.array(img.crop((x0, y0, x1, y1)).convert("RGB"))
    ink = rgb.min(axis=2) < 195
    spans: list[tuple[int, int]] = []
    start = None
    for i, on in enumerate(ink.any(axis=0)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, ink.shape[1]))
    centers: list[tuple[int, int]] = []
    for left, right in spans:
        width = right - left
        if not 8 <= width <= 34:
            continue
        rows = np.where(ink[:, left:right].any(axis=1))[0]
        if len(rows) == 0:
            continue
        height = int(rows[-1] - rows[0]) + 1
        if not 8 <= height <= 32:
            continue
        centers.append((x0 + (left + right) // 2, y0 + int((rows[0] + rows[-1]) // 2)))
    return centers


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
            w.click_window_relative(hwnd, cx, cy)
            return True

    # Heuristic: open-button cluster near right side of field row
    cx = min(img.width - 28, x + ww + 520)
    cy = y + hh // 2
    w.click_window_relative(hwnd, cx, cy)
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
            w.click_window_relative(hwnd, cx, cy)
            return True

    if not box:
        return False
    x, y, ww, hh = box
    # First icon after masked value (лупа), typically ~200-260px right of label end
    cx = min(img.width - 20, x + ww + 230)
    cy = y + hh // 2
    w.click_window_relative(hwnd, cx, cy)
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
    region = region_right_of_label(img, sum_label, width=280, height=48, lang=lang)
    if region is None:
        region = img.crop((0, img.height // 3, img.width, img.height))
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
) -> ActResult:
    delays = cfg["delays"]
    lang = cfg["ocr"].get("lang", "eng+rus")
    act_prefix = cfg["act_title_prefix"]
    ticket_prefix = cfg["ticket_title_prefix"]
    list_sub = cfg["list_window_title_substr"]

    pause.wait_if_paused()
    if pause.stop:
        return ActResult("", "", "", status="stopped", collected_at=_now())

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
            act_title="(не открылся)",
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
    amount = read_amount_from_act(act_img, cfg["sum_label"], lang)
    print(f"Акт: {act.title}. Сумма: {amount or '-'}")

    print("Жму кнопку справа у «Залоговый билет»")
    ticket_box = ocr.find_label_box(act_img, cfg["ticket_field_label"], lang=lang)
    if ticket_box:
        x, y, ww, hh = ticket_box
        icons = row_icons(act_img, x + ww + 30, y - 10, act_img.width - 6, y + hh + 16)
        if icons:
            w.click_window_relative(act.hwnd, icons[-1][0], icons[-1][1])
        else:
            w.open_reference(act.hwnd)
    else:
        w.open_reference(act.hwnd)
    ticket = w.wait_for_window(ticket_prefix, timeout=4)
    if not ticket:
        save_debug(act_img, "act_no_ticket")
        close_opened_forms([act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=act.title,
            amount=amount,
            phone="",
            status="error",
            error="Не удалось открыть Залоговый билет",
            collected_at=_now(),
        )

    time.sleep(delays["after_open"])
    pause.wait_if_paused()

    print(f"Залоговый билет: {ticket.title}. Жму кнопку у клиента")
    ticket_img = w.screenshot_window(ticket.hwnd)
    before_client = snapshot_hwnds()
    client_box = ocr.find_label_box(ticket_img, cfg["client_field_label"], lang=lang)
    if client_box:
        x, y, ww, hh = client_box
        # Кнопка открытия стоит сразу за крестиком, до поля «Дата рождения».
        icons = row_icons(ticket_img, x + ww + 8, y - 8, x + ww + 520, y + hh + 16)
        if icons:
            w.click_window_relative(ticket.hwnd, icons[-1][0], icons[-1][1])
        else:
            w.open_reference(ticket.hwnd)
    else:
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
            act_title=act.title,
            amount=amount,
            phone="",
            ticket_title=ticket.title,
            status="error",
            error="Не удалось открыть карточку клиента",
            collected_at=_now(),
        )

    time.sleep(delays["after_open"])
    pause.wait_if_paused()

    print(f"Карточка: {client.title}. Жму глаз у телефона")
    client_img = w.screenshot_window(client.hwnd)
    phone_box = ocr.find_label_box(client_img, cfg["phone_label"], lang=lang)
    eye = None
    if phone_box:
        x, y, ww, hh = phone_box
        icons = row_icons(client_img, x + ww + 20, y - 12, x + ww + 780, y + hh + 20)
        if icons:
            eye = icons[0]
            w.click_window_relative(client.hwnd, eye[0], eye[1])
    if eye is None and not click_reveal_phone(client.hwnd, client_img, cfg["phone_label"], lang=lang):
        save_debug(client_img, "client_no_reveal")
        close_opened_forms([client, ticket, act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=act.title,
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
                max(0, eye[0] - 190),
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
        act_title=act.title,
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


def move_to_next_row(list_win: w.WindowInfo) -> None:
    w.brief_focus(list_win.hwnd)
    time.sleep(0.1)
    w.press_down(list_win.hwnd)


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

        result = process_one_act(cfg, list_win, pause)
        if pause.stop or result.status == "stopped":
            break

        if result.act_title in done and result.status == "ok":
            print(f"Пропуск (уже было): {result.act_title}")
            move_to_next_row(list_win)
            time.sleep(cfg["delays"]["between_acts"])
            empty_streak += 1
            if empty_streak >= 5:
                print("Похоже, новые акты закончились.")
                break
            continue

        if result.act_title and result.act_title != "(не открылся)":
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

        move_to_next_row(list_win)
        time.sleep(cfg["delays"]["between_acts"])

    print(f"Готово. CSV: {csv_path}")
