from __future__ import annotations

import csv
import json
import os
import re
import time
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
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


TABLE_COLUMNS = [
    "Акт",
    "Номер",
    "Дата",
    "Сумма на руки",
    "Телефон",
    "Залоговый билет",
    "Примечание",
    "Собрано",
]


def ensure_output(cfg: dict) -> tuple[Path, Path]:
    csv_path = ROOT / cfg["output_csv"]
    log_path = ROOT / cfg["processed_log"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerow(TABLE_COLUMNS)
    log_path.touch(exist_ok=True)
    return csv_path, log_path


def already_processed(log_path: Path) -> set[str]:
    return {
        line.strip()
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def format_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return " ".join((raw or "").split())
    return f"+7 {digits[0:3]} {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"


def format_sum(raw: str) -> str:
    token = re.sub(r"[^\d.,]", "", raw or "").replace(".", ",")
    if not token:
        return ""
    whole, _, frac = token.partition(",")
    whole = whole.lstrip("0") or "0"
    frac = ((frac or "") + "00")[:2]
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return " ".join(reversed(groups)) + "," + frac


def split_act_title(title: str) -> tuple[str, str, str]:
    """Полное название, номер вида АЗВ-0003788 и дата из строки списка."""
    text = " ".join((title or "").split())
    match = re.match(
        r"^(?P<name>Акт\s+оценки)\s+(?P<num>\S+)\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})"
        r"(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return text, "", ""
    when = match.group("date")
    if match.group("time"):
        when = f"{when} {match.group('time')}"
    return text, match.group("num"), when


def table_row(result: ActResult) -> list[str]:
    _full, number, when = split_act_title(result.act_title)
    return [
        result.act_title,
        number,
        when,
        format_sum(result.amount),
        format_phone(result.phone),
        result.ticket_title,
        result.error.strip(),
        result.collected_at,
    ]


def _read_table(csv_path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)
        for row in reader:
            if any(cell.strip() for cell in row):
                rows.append(row)
    return rows


def _xlsx_cell(col: int, row: int, value: str, style: int) -> str:
    letters = ""
    number = col
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    text = xml_escape("".join(ch for ch in value if ch in "\t\n" or ord(ch) >= 32))
    return f'<c r="{letters}{row}" t="inlineStr" s="{style}"><is><t>{text}</t></is></c>'


def write_xlsx(csv_path: Path) -> Path:
    rows = _read_table(csv_path)
    sheet_rows = []
    header = "".join(_xlsx_cell(i + 1, 1, name, 1) for i, name in enumerate(TABLE_COLUMNS))
    sheet_rows.append(f'<row r="1">{header}</row>')
    for index, row in enumerate(rows, start=2):
        padded = list(row) + [""] * (len(TABLE_COLUMNS) - len(row))
        cells = "".join(
            _xlsx_cell(i + 1, index, padded[i], 0) for i in range(len(TABLE_COLUMNS))
        )
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    last_row = max(1, len(rows) + 1)
    last_col = chr(64 + len(TABLE_COLUMNS))
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>
<col min="1" max="1" width="46" customWidth="1"/>
<col min="2" max="2" width="16" customWidth="1"/>
<col min="3" max="3" width="20" customWidth="1"/>
<col min="4" max="4" width="16" customWidth="1"/>
<col min="5" max="5" width="20" customWidth="1"/>
<col min="6" max="6" width="42" customWidth="1"/>
<col min="7" max="7" width="28" customWidth="1"/>
<col min="8" max="8" width="20" customWidth="1"/>
</cols>
<sheetData>{"".join(sheet_rows)}</sheetData>
<autoFilter ref="A1:{last_col}{last_row}"/>
</worksheet>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf></cellXfs>
</styleSheet>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Акты" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    target = csv_path.with_suffix(".xlsx")
    temporary = target.with_name(target.stem + ".writing.xlsx")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", root_rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/styles.xml", styles)
        book.writestr("xl/worksheets/sheet1.xml", sheet)
    return publish_table(temporary, target)


def publish_table(temporary: Path, target: Path) -> Path:
    """Подменить таблицу. Если Excel держит файл, писать рядом и не падать."""
    try:
        os.replace(temporary, target)
        return target
    except PermissionError:
        pass
    fallback = target.with_name(target.stem + "-новый.xlsx")
    try:
        os.replace(temporary, fallback)
    except PermissionError:
        print(
            f"Таблица {target.name} открыта, записать её не получилось. Данные остались в csv.",
            flush=True,
        )
        return target
    print(
        f"Таблица {target.name} открыта в Excel, записал в {fallback.name}.",
        flush=True,
    )
    return fallback


def collected_acts(csv_path: Path) -> set[str]:
    """Акты, у которых в таблице уже лежит телефон из 10 цифр на 9."""
    if not csv_path.exists():
        return set()
    found: set[str] = set()
    for row in _read_table(csv_path):
        if row and revealed_phone(row[4] if len(row) > 4 else ""):
            found.add(row[0])
    return found


def append_result(csv_path: Path, log_path: Path, result: ActResult) -> None:
    rows = _read_table(csv_path) if csv_path.exists() else []
    new_row = table_row(result)
    body: list[list[str]] = []
    replaced = False
    for row in rows:
        if row and row[0] == result.act_title:
            body.append(new_row)
            replaced = True
        else:
            body.append(row)
    if not replaced:
        body.append(new_row)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(TABLE_COLUMNS)
        writer.writerows(body)
    phone_ok = bool(revealed_phone(result.phone))
    known = already_processed(log_path)
    if phone_ok and result.act_title not in known:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(result.act_title + "\n")
    if not phone_ok and result.act_title in known:
        kept = [line for line in known if line != result.act_title]
        log_path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    try:
        table = write_xlsx(csv_path)
    except OSError as exc:
        print(f"Таблицу не записал ({exc}). Сбор идёт дальше.", flush=True)
        return
    print(f"Записано в таблицу: {table}", flush=True)


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
    solid = peach.mean(axis=1) > 0.55
    start = None
    for y, on in enumerate(solid.tolist()):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 12:
                return start, min(img.height, start + 32)
            start = None
    if start is not None and img.height - start >= 12:
        return start, min(img.height, start + 32)
    return None


def client_field_point(img: Image.Image) -> Optional[tuple[int, int]]:
    """Точка в поле «Клиент:», на фамилии, левее крестика."""
    band = _peach_top(img)
    if band is None:
        return None
    y0, _y1 = band
    y1 = min(img.height, y0 + 30)
    rgb = np.array(img.crop((0, y0, img.width, y1)).convert("RGB"))
    height, width = rgb.shape[:2]
    if height < 8 or width < 8:
        return None
    ink = rgb.min(axis=2) < 175
    filled = ink.mean(axis=0) > 0.1
    closed = filled.copy()
    gap = 0
    for x in range(width):
        if filled[x]:
            if 0 < gap <= 8:
                closed[x - gap : x] = True
            gap = 0
        else:
            gap += 1
    runs: list[tuple[int, int]] = []
    x = 0
    while x < width:
        if not closed[x]:
            x += 1
            continue
        start = x
        while x < width and closed[x]:
            x += 1
        if x - start >= 12:
            runs.append((start, x - 1))
    field = [run for run in runs if 70 < run[0] < width * 0.5]
    if not field:
        return None
    label = field[0]
    if len(field) > 1 and field[1][0] - label[1] < 40:
        name_start = field[1][0]
        name_end = field[1][1]
        cx = name_start + min(24, max(8, (name_end - name_start) // 5))
    else:
        cx = (label[0] + label[1]) // 2
    return int(cx), int(y0 + height // 2)


def focus_client_field(hwnd: int, img: Image.Image) -> bool:
    """Щелчок по полю «Клиент:», без крестика и без квадрата открытия."""
    point = client_field_point(img)
    if point is None:
        print("Поле «Клиент» на залоговом билете не вижу, Ctrl+Shift+F4 не жму.", flush=True)
        return False
    print(f"Поле «Клиент»: {point[0]}, {point[1]}", flush=True)
    w.click_window_relative(hwnd, point[0], point[1], image_size=img.size)
    return True


def _saturation(img: Image.Image, x: int, y: int) -> float:
    patch = np.array(
        img.crop((max(0, x - 10), max(0, y - 10), min(img.width, x + 10), min(img.height, y + 10))).convert("RGB")
    ).astype(int)
    if patch.size == 0:
        return 0.0
    return float((patch.max(axis=2) - patch.min(axis=2)).mean())


def phone_icon_cluster(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Четыре значка справа от номера: мессенджер, лупа, карандаш, трубка."""
    height, width = img.height, img.width
    best: list[tuple[int, int, int, int]] = []
    best_score = -1
    for y0 in range(int(height * 0.40), int(height * 0.86), 8):
        icons = [
            icon
            for icon in row_icons(img, 40, y0, int(width * 0.92), y0 + 30)
            if 12 <= icon[2] <= 36 and 12 <= icon[3] <= 36
        ]
        group: list[tuple[int, int, int, int]] = []
        groups: list[list[tuple[int, int, int, int]]] = []
        for icon in icons:
            if group and icon[0] - group[-1][0] > 70:
                groups.append(group)
                group = [icon]
            else:
                group.append(icon)
        if group:
            groups.append(group)
        for item in groups:
            if not 3 <= len(item) <= 4:
                continue
            if item[-1][0] - item[0][0] > 220:
                continue
            if max(icon[1] for icon in item) - min(icon[1] for icon in item) > 12:
                continue
            score = len(item) * 10
            if len(item) >= 2 and _saturation(img, item[0][0], item[0][1]) > _saturation(img, item[1][0], item[1][1]) + 8:
                score += 5
            if score > best_score:
                best_score = score
                best = item
    return best


def phone_digit_box(
    img: Image.Image,
    cluster: list[tuple[int, int, int, int]],
) -> Optional[tuple[int, int, int, int]]:
    """Прямоугольник цифр сразу слева от значков. ИНН справа от значков сюда не входит."""
    if not cluster:
        return None
    anchor_x = min(icon[0] - icon[2] // 2 for icon in cluster)
    anchor_y = cluster[0][1]
    y0 = max(0, anchor_y - 16)
    y1 = min(img.height, anchor_y + 16)
    x1 = max(0, anchor_x - 8)
    if x1 < 30 or y1 <= y0:
        return None
    rgb = np.array(img.crop((0, y0, x1, y1)).convert("RGB"))
    red = rgb[:, :, 0].astype(int)
    green = rgb[:, :, 1].astype(int)
    blue = rgb[:, :, 2].astype(int)
    ink = (rgb.min(axis=2) < 175) | ((green > red + 15) & (green > 80) & (red < 220))
    if ink.size == 0:
        return None
    filled = ink.mean(axis=0) > 0.04
    gap = 0
    closed = filled.copy()
    for x in range(filled.shape[0]):
        if filled[x]:
            if 0 < gap <= 6:
                closed[x - gap : x] = True
            gap = 0
        else:
            gap += 1
    runs: list[tuple[int, int]] = []
    x = 0
    width = closed.shape[0]
    while x < width:
        if not closed[x]:
            x += 1
            continue
        start = x
        while x < width and closed[x]:
            x += 1
        if x - start >= 24:
            runs.append((start, x - 1))
    if not runs:
        return None
    # Ближний к значкам фрагмент — это сам номер, не подпись «Телефон» слева.
    start, end = runs[-1]
    if end - start > 160:
        start = end - 160
    return start, y0, min(x1, end + 2), y1


def revealed_phone(text: str, tail: str = "") -> str:
    """10 цифр с девятки. Хвост маски (0661) обязан совпасть. ИНН из 12 цифр не берётся."""
    digits = re.sub(r"\D", "", text or "")
    tail_digits = re.sub(r"\D", "", tail or "")
    for index in range(0, max(0, len(digits) - 9) + 1):
        chunk = digits[index : index + 10]
        if len(chunk) < 10 or not chunk.startswith("9"):
            continue
        if tail_digits and not chunk.endswith(tail_digits):
            continue
        return chunk
    return ""


def masked_tail(text: str) -> str:
    """Видимый конец маски: из «*******0661» получается 0661."""
    match = re.search(r"\*+(\d{2,4})", text or "")
    if match:
        return match.group(1)
    return ""


def click_phone_magnifier(hwnd: int, img: Image.Image) -> Optional[tuple[int, int]]:
    """Лупа — второй значок в ряду справа от номера, сразу за мессенджером."""
    cluster = phone_icon_cluster(img)
    if len(cluster) < 2:
        return None
    target = cluster[1]
    print(f"Лупа телефона: {target[0]}, {target[1]}", flush=True)
    w.click_window_relative(hwnd, target[0], target[1], image_size=img.size)
    return target[0], target[1]


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


def read_act_titles(path: Path) -> list[str]:
    """Названия актов из первого столбца таблицы, в порядке строк."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    titles: list[str] = []
    seen: set[str] = set()
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", ns)))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in book.namelist():
            sheets = [name for name in book.namelist() if name.startswith("xl/worksheets/sheet")]
            if not sheets:
                return []
            sheet_name = sheets[0]
        root = ET.fromstring(book.read(sheet_name))
        for row in root.findall("m:sheetData/m:row", ns):
            for cell in row.findall("m:c", ns):
                kind = cell.get("t") or ""
                if kind == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                else:
                    node = cell.find("m:v", ns)
                    value = node.text if node is not None and node.text else ""
                    if kind == "s" and value.isdigit():
                        index = int(value)
                        value = shared[index] if index < len(shared) else ""
                clean = " ".join(value.split())
                if not clean.casefold().startswith("акт") or clean in seen:
                    continue
                seen.add(clean)
                titles.append(clean)
    return titles


def act_number(title: str) -> str:
    match = re.search(r"[A-Za-zА-Яа-яЁё]{2,8}-\d{3,}", title or "")
    return match.group(0) if match else ""


def search_queries(title: str) -> list[str]:
    """Сначала полное название, затем номер вида БТС-0008818."""
    text = " ".join((title or "").split())
    queries = [text] if text else []
    number = act_number(text)
    if number and number not in queries:
        queries.append(number)
    return queries


def load_requested_acts(cfg: dict) -> list[str]:
    name = cfg.get("act_list") or "список-актов.xlsx"
    path = ROOT / name
    if not path.exists():
        return []
    return read_act_titles(path)


def _search_dialog(report_hwnd: int = 0) -> Optional[w.WindowInfo]:
    dialogs = [
        item
        for item in w.find_windows("Поиск")
        if "поиск" in item.title.casefold()
        and item.width < 900
        and item.hwnd != report_hwnd
    ]
    if not dialogs:
        return None
    return min(dialogs, key=lambda item: item.width * item.height)


def _wait_search_dialog(report_hwnd: int, timeout: float = 1.6) -> Optional[w.WindowInfo]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        dialog = _search_dialog(report_hwnd)
        if dialog is not None:
            return dialog
        time.sleep(0.2)
    return None


def _word_point(word: dict) -> tuple[int, int]:
    return (
        word["left"] + max(word["width"], 1) // 2,
        word["top"] + max(word["height"], 1) // 2,
    )


def report_find_button(words: list[dict], height: int) -> Optional[dict]:
    """Кнопка «Найти...» в командной панели отчёта, не общий поиск 1С."""
    limit = int(height * 0.35)
    found = [
        word
        for word in words
        if word["text"].casefold().startswith("найт") and word["top"] < limit
    ]
    if not found:
        return None
    return min(found, key=lambda word: word["top"])


def seek_button(words: list[dict]) -> Optional[dict]:
    """Кнопка «Искать» в окне поиска. Рядом обычно «Отмена»."""
    buttons = [word for word in words if word["text"].casefold().startswith("иска")]
    if not buttons:
        return None
    cancels = [
        word
        for word in words
        if word["text"].casefold().startswith(("отмен", "закры"))
    ]
    if not cancels:
        return buttons[-1]

    def near(button: dict) -> int:
        bx, by = _word_point(button)
        return min(abs(bx - cx) + abs(by - cy) for cx, cy in map(_word_point, cancels))

    return min(buttons, key=near)


def _focus_report_table(list_win: w.WindowInfo) -> None:
    """Щелчок по ячейке отчёта. Тогда Ctrl+F открывает поиск таблицы."""
    image = w.screenshot_window(list_win.hwnd)
    x = max(40, int(image.width * 0.28))
    y = max(40, int(image.height * 0.58))
    w.click_window_relative(list_win.hwnd, x, y, image_size=image.size)
    time.sleep(0.3)


def _click_word(hwnd: int, word: dict, image_size: tuple[int, int]) -> None:
    x, y = _word_point(word)
    w.click_window_relative(hwnd, x, y, image_size=image_size)


def _open_report_search(list_win: w.WindowInfo, lang: str) -> Optional[w.WindowInfo]:
    """Поиск внутри отчёта. Общий поиск 1С и Escape по отчёту не используются."""
    dialog = _search_dialog(list_win.hwnd)
    if dialog is not None:
        return dialog
    print("Щёлкаю таблицу отчёта и открываю поиск.", flush=True)
    _focus_report_table(list_win)
    w.press_find(list_win.hwnd)
    dialog = _wait_search_dialog(list_win.hwnd)
    if dialog is not None:
        return dialog
    image = w.screenshot_window(list_win.hwnd)
    words = ocr.word_boxes(image, lang=lang, scale=1.5)
    button = report_find_button(words, image.height)
    if button is None:
        print("Поиск внутри отчёта не открылся. Отчёт не закрываю.", flush=True)
        return None
    print("Жму «Найти...» в самом отчёте.", flush=True)
    _click_word(list_win.hwnd, button, image.size)
    return _wait_search_dialog(list_win.hwnd)


def _run_query(dialog: w.WindowInfo, query: str, lang: str) -> None:
    image = w.screenshot_window(dialog.hwnd)
    w.click_window_relative(
        dialog.hwnd,
        max(20, int(image.width * 0.45)),
        max(16, int(image.height * 0.22)),
        image_size=image.size,
    )
    time.sleep(0.1)
    w.paste_text(dialog.hwnd, query)
    time.sleep(0.15)
    words = ocr.word_boxes(image, lang=lang, scale=1.5)
    button = seek_button(words)
    if button is None:
        print("Кнопку «Искать» не вижу, жму Enter в окне поиска.", flush=True)
        w.press_enter(dialog.hwnd)
        return
    print("Жму «Искать».", flush=True)
    _click_word(dialog.hwnd, button, image.size)


def _close_search_dialog(dialog: Optional[w.WindowInfo], report_hwnd: int) -> None:
    """Закрыть только окно «Поиск». Escape по отчёту закрывает сам отчёт."""
    if dialog is None or dialog.hwnd == report_hwnd:
        return
    w.press_escape(dialog.hwnd)
    time.sleep(0.25)


def _row_matches(found: str, query: str, number: str) -> bool:
    text = found.casefold()
    if number and number.casefold() in text:
        return True
    return bool(found) and text.startswith("акт") and query.casefold() in text


def _search_on_open_panel(
    list_win: w.WindowInfo,
    title: str,
    lang: str,
    number: str,
) -> bool:
    """Панель «Искать» внутри отчёта. Escape не нажимается."""
    for query in search_queries(title):
        image = w.screenshot_window(list_win.hwnd)
        words = ocr.word_boxes(image, lang=lang, scale=1.5)
        button = seek_button(words)
        if button is None:
            print("Окно поиска в отчёте не вижу. Отчёт не закрываю.", flush=True)
            return False
        _, field_y = _word_point(button)
        w.click_window_relative(
            list_win.hwnd,
            max(10, button["left"] - 90),
            field_y,
            image_size=image.size,
        )
        time.sleep(0.1)
        print(f"Поиск в отчёте: {query}", flush=True)
        w.paste_text(list_win.hwnd, query)
        time.sleep(0.15)
        _click_word(list_win.hwnd, button, image.size)
        time.sleep(0.7)
        image = w.screenshot_window(list_win.hwnd)
        words = ocr.word_boxes(image, lang=lang, scale=1.5)
        cancels = [
            word
            for word in words
            if word["text"].casefold().startswith(("отмен", "закры"))
        ]
        if cancels and button is not None:
            bx, by = _word_point(button)
            cancel = min(
                cancels,
                key=lambda word: abs(_word_point(word)[0] - bx) + abs(_word_point(word)[1] - by),
            )
            _click_word(list_win.hwnd, cancel, image.size)
            time.sleep(0.25)
        found = row_act_name(list_win, lang)
        if _row_matches(found, query, number):
            print(f"Строка найдена: {found}", flush=True)
            return True
        print("Этот запрос строку не выделил. Отчёт остаётся открытым.", flush=True)
    return False


def search_for_act(list_win: w.WindowInfo, title: str, lang: str) -> bool:
    """Поиск в открытом отчёте: Ctrl+F, вставка, «Искать», выделенная строка."""
    number = act_number(title)
    dialog = _open_report_search(list_win, lang)
    if dialog is None:
        return _search_on_open_panel(list_win, title, lang, number)
    for query in search_queries(title):
        print(f"Поиск в отчёте: {query}", flush=True)
        _run_query(dialog, query, lang)
        time.sleep(0.7)
        _close_search_dialog(dialog, list_win.hwnd)
        found = row_act_name(list_win, lang)
        if _row_matches(found, query, number):
            print(f"Строка найдена: {found}", flush=True)
            return True
        print("Этот запрос строку не выделил. Отчёт остаётся открытым.", flush=True)
        dialog = _open_report_search(list_win, lang)
        if dialog is None:
            return False
    _close_search_dialog(dialog, list_win.hwnd)
    return False


def process_one_act(
    cfg: dict,
    list_win: w.WindowInfo,
    pause: PauseController,
    done: Optional[set[str]] = None,
    expected: str = "",
) -> ActResult:
    delays = cfg["delays"]
    lang = cfg["ocr"].get("lang", "eng+rus")
    act_prefix = cfg["act_title_prefix"]
    ticket_prefix = cfg["ticket_title_prefix"]
    list_sub = cfg["list_window_title_substr"]

    pause.wait_if_paused()
    if pause.stop:
        return ActResult("", "", "", status="stopped", collected_at=_now())

    if expected:
        list_name = " ".join(expected.split())
        print(f"Открываю найденный акт: {list_name}", flush=True)
    else:
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
    if not expected:
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

    print(f"Залоговый билет: {ticket.title}. Сначала поле «Клиент», затем Ctrl+Shift+F4", flush=True)
    ticket_img = w.screenshot_window(ticket.hwnd)
    before_client = snapshot_hwnds()
    if not focus_client_field(ticket.hwnd, ticket_img):
        save_debug(ticket_img, "ticket_no_client_field")
        close_opened_forms([ticket, act], list_win.hwnd, delays["after_close"])
        return ActResult(
            act_title=list_name,
            amount=amount,
            phone="",
            ticket_title=ticket.title,
            status="error",
            error="Не вижу поле «Клиент» на залоговом билете",
            collected_at=_now(),
        )
    time.sleep(0.25)
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
    before_box = phone_digit_box(client_img, phone_icon_cluster(client_img))
    tail = ""
    if before_box is not None:
        masked = client_img.crop(before_box)
        tail = masked_tail(
            ocr.read_text(masked, lang=lang, whitelist="0123456789*", psm=7)
        )
        if tail:
            print(f"До лупы виден конец номера: {tail}", flush=True)
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
    digit_box = phone_digit_box(client_img2, phone_icon_cluster(client_img2))
    shown = client_img2.crop(digit_box) if digit_box is not None else None
    if shown is not None:
        enlarged = shown.resize((shown.width * 3, shown.height * 3), Image.Resampling.LANCZOS)
        heard = ocr.read_text(enlarged, lang=lang, whitelist="0123456789", psm=7)
        phone = revealed_phone(heard, tail)
        if not phone and tail:
            phone = revealed_phone(heard)
        print(f"Цифры после лупы: {heard.strip() or '-'} -> {phone or 'не номер'}", flush=True)
    if not phone and shown is not None:
        folder = ROOT / "output" / "номера"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-]+", "_", list_name)[:40] or "номер"
        shown.save(folder / f"{safe}.png")
        print(f"Цифры не прочитаны, сохранён узкий фрагмент: {folder / (safe + '.png')}", flush=True)

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


def back_to_list(list_win: w.WindowInfo) -> None:
    """Вернуть фокус в отчёт. Escape не жмём: в 1С он закрывает отчёт."""
    w.brief_focus(list_win.hwnd)


def advance_to_next_act(list_win: w.WindowInfo, lang: str, previous: str) -> str:
    """Вниз, пока текущая строка снова не начнётся со слова «Акт»."""
    print("Ищу строку, которая начинается с «Акт».", flush=True)
    stuck = 0
    empty = 0
    last = previous
    for _ in range(60):
        w.brief_focus(list_win.hwnd)
        w.press_down(list_win.hwnd)
        time.sleep(0.25)
        name = row_act_name(list_win, lang)
        if name and name != previous:
            print(f"Следующий акт: {name}", flush=True)
            return name
        if not name:
            empty += 1
            stuck = 0
            last = ""
            if empty % 8 == 0:
                print("Список не отвечает, закрываю лишнее и иду дальше.", flush=True)
                back_to_list(list_win)
            continue
        empty = 0
        if name == last:
            stuck += 1
            if stuck >= 8:
                print("Строка списка не меняется, шагаю вниз ещё раз.", flush=True)
                back_to_list(list_win)
                w.press_down(list_win.hwnd)
                stuck = 0
        else:
            stuck = 0
        last = name
    print("Пока не вижу следующую строку «Акт», сбор не останавливаю.", flush=True)
    back_to_list(list_win)
    return ""


def remember_result(
    csv_path: Path,
    log_path: Path,
    done: set[str],
    result: ActResult,
    processed: int,
) -> int:
    if result.status == "skip":
        print(f"Пропуск (уже было): {result.act_title}", flush=True)
        return processed
    if not result.act_title or result.act_title == "(не открылся)":
        print(f"Ошибка: {result.error}", flush=True)
        print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)
        return processed
    append_result(csv_path, log_path, result)
    if revealed_phone(result.phone):
        done.add(result.act_title)
    else:
        done.discard(result.act_title)
        print("Номер не собран, этот акт будет взят снова.", flush=True)
    processed += 1
    print(
        f"[{processed}] {result.status}: {result.act_title} | "
        f"сумма={result.amount or '-'} | тел={result.phone or '-'}",
        flush=True,
    )
    if result.error:
        print(f"  ! {result.error}", flush=True)
        print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)
    return processed


def run_requested_list(
    cfg: dict,
    pause: PauseController,
    list_win: w.WindowInfo,
    csv_path: Path,
    log_path: Path,
    done: set[str],
    titles: list[str],
) -> None:
    """Идёт по файлу: Ctrl+F, вставка акта, «Искать», затем обычный сбор номера."""
    lang = cfg["ocr"].get("lang", "eng+rus")
    print(f"Актов в файле: {len(titles)}. Листание вниз не используется.", flush=True)
    processed = 0
    for index, title in enumerate(titles, start=1):
        pause.wait_if_paused()
        if pause.stop:
            break
        print(f"Список {index}/{len(titles)}: {title}", flush=True)
        if title in done:
            print(f"Пропуск (уже было): {title}", flush=True)
            continue
        try:
            if not search_for_act(list_win, title, lang):
                processed = remember_result(
                    csv_path,
                    log_path,
                    done,
                    ActResult(
                        act_title=title,
                        amount="",
                        phone="",
                        status="error",
                        error="Поиск не выделил этот акт",
                        collected_at=_now(),
                    ),
                    processed,
                )
                continue
            result = process_one_act(cfg, list_win, pause, done, expected=title)
            if pause.stop or result.status == "stopped":
                break
            processed = remember_result(csv_path, log_path, done, result, processed)
        except Exception as exc:
            print(f"Ошибка: {exc}", flush=True)
            print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)
            try:
                back_to_list(list_win)
            except Exception:
                pass


def run(cfg: dict, pause: PauseController) -> None:
    ocr.configure_tesseract(cfg.get("ocr", {}).get("tesseract_cmd") or "")
    csv_path, log_path = ensure_output(cfg)
    done = collected_acts(csv_path)

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

    list_path = ROOT / (cfg.get("act_list") or "список-актов.xlsx")
    titles = read_act_titles(list_path) if list_path.exists() else []
    if not titles:
        print(f"Не вижу список актов в файле {list_path}. Листание вниз не запускаю.", flush=True)
        return
    run_requested_list(cfg, pause, list_win, csv_path, log_path, done, titles)
    print(f"Готово. Таблица: {csv_path.with_suffix('.xlsx')}")
    return

    max_acts = int(cfg.get("max_acts") or 0)
    processed = 0
    missed_steps = 0

    while not pause.stop:
        pause.wait_if_paused()
        if pause.stop:
            break

        result = None
        lang = cfg["ocr"].get("lang", "eng+rus")
        try:
            result = process_one_act(cfg, list_win, pause, done)
            if pause.stop or result.status == "stopped":
                break

            if result.status == "skip":
                print(f"Пропуск (уже было): {result.act_title}")
            elif result.act_title and result.act_title != "(не открылся)":
                append_result(csv_path, log_path, result)
                if revealed_phone(result.phone):
                    done.add(result.act_title)
                else:
                    done.discard(result.act_title)
                    print("Номер не собран, этот акт будет взят снова.", flush=True)
                processed += 1
                print(
                    f"[{processed}] {result.status}: {result.act_title} | "
                    f"сумма={result.amount or '-'} | тел={result.phone or '-'}"
                )
                if result.error:
                    print(f"  ! {result.error}")
                    print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)
            else:
                print(f"Ошибка: {result.error}", flush=True)
                print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)

            if max_acts and processed >= max_acts:
                print(f"Достигнут лимит max_acts={max_acts}")
                break

            if result.status == "error":
                back_to_list(list_win)
            moved = advance_to_next_act(list_win, lang, result.act_title)
        except Exception as exc:
            print(f"Ошибка: {exc}", flush=True)
            print("Ошибка не останавливает сбор, перехожу к следующему акту.", flush=True)
            moved = False
            try:
                back_to_list(list_win)
                title = result.act_title if result is not None else ""
                moved = bool(advance_to_next_act(list_win, lang, title))
            except Exception as follow:
                print(f"К следующему акту перейти не вышло: {follow}", flush=True)
        if moved:
            missed_steps = 0
        else:
            missed_steps += 1
            print(
                f"Следующий акт пока не виден, продолжаю ({missed_steps}).",
                flush=True,
            )
            if missed_steps >= 8:
                print("Список актов больше не листается — остановка.", flush=True)
                break
        time.sleep(cfg["delays"]["between_acts"])

    print(f"Готово. Таблица: {csv_path.with_suffix('.xlsx')}")
