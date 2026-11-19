from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image


PHONE_RE = re.compile(
    r"(?:\+?7|8)?[\s\-()]*(?:\d[\s\-()]*){10}|\*{4,}\d{2,4}|\d{10,11}"
)
SUM_RE = re.compile(r"\d{1,3}(?:[ \u00A0]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?")


def _ocr(call, lang: str):
    """eng+rus, затем каждый язык отдельно. Наружу уходит первая ошибка."""
    order = []
    for attempt in (lang, "rus", "eng"):
        if attempt and attempt not in order:
            order.append(attempt)
    first = None
    for attempt in order:
        try:
            return call(attempt)
        except Exception as exc:
            if first is None:
                first = exc
    raise first


def configure_tesseract(cmd: str = "") -> None:
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _to_cv(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def preprocess_for_ocr(image: Image.Image, scale: float = 2.0) -> np.ndarray:
    bgr = _to_cv(image)
    if scale != 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return thr


def read_text(image: Image.Image, lang: str = "eng+rus", whitelist: str = "", psm: int = 6) -> str:
    cfg = f"--psm {psm}"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    prepared = preprocess_for_ocr(image)
    return _ocr(lambda attempt: pytesseract.image_to_string(prepared, lang=attempt, config=cfg) or "", lang)


def normalize_phone(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    # partially masked like ******4015 — keep as-is marker
    if "*" in (raw or "") and re.search(r"\d{2,4}$", raw or ""):
        return re.sub(r"[^\d*]", "", raw)
    if 10 <= len(digits) <= 11:
        return digits
    return None


def extract_phone(image: Image.Image, lang: str = "eng+rus") -> Optional[str]:
    text = read_text(image, lang=lang, whitelist="0123456789+* ()-")
    candidates = []
    for match in PHONE_RE.findall(text.replace("\n", " ")):
        norm = normalize_phone(match)
        if norm:
            candidates.append(norm)
    # prefer full unmasked numbers
    full = [c for c in candidates if c.isdigit() and len(c) >= 10 and "*" not in c]
    if full:
        return full[0]
    return candidates[0] if candidates else None


def extract_sum(image: Image.Image, lang: str = "eng+rus") -> Optional[str]:
    text = read_text(image, lang=lang, whitelist="0123456789., ")
    matches = SUM_RE.findall(text.replace("\n", " "))
    if not matches:
        return None
    # take the longest numeric-looking token
    best = max(matches, key=lambda s: len(re.sub(r"\D", "", s)))
    return best.replace("\u00A0", " ").strip()


def find_template(
    haystack: Image.Image,
    needle: Image.Image,
    threshold: float = 0.78,
) -> Optional[tuple[int, int, int, int]]:
    """Return (x, y, w, h) of best match in haystack coords, or None."""
    h_img = _to_cv(haystack)
    n_img = _to_cv(needle)
    if n_img.shape[0] >= h_img.shape[0] or n_img.shape[1] >= h_img.shape[1]:
        return None
    res = cv2.matchTemplate(h_img, n_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        return None
    x, y = max_loc
    h, w = n_img.shape[:2]
    return x, y, w, h


def find_label_box(
    image: Image.Image,
    label: str,
    lang: str = "eng+rus",
    scale: float = 1.5,
) -> Optional[tuple[int, int, int, int]]:
    """OCR word boxes and find a box whose text contains label fragments."""
    words = word_boxes(image, lang=lang, scale=scale)
    return box_for_label(words, label)


def word_boxes(image: Image.Image, lang: str = "eng+rus", scale: float = 1.0) -> list[dict]:
    prepared = preprocess_for_ocr(image, scale=scale)
    data = _ocr(
        lambda attempt: pytesseract.image_to_data(
            prepared, lang=attempt, output_type=pytesseract.Output.DICT, config="--psm 6"
        ),
        lang,
    )
    words = []
    for i, txt in enumerate(data.get("text", [])):
        txt = (txt or "").strip()
        if not txt:
            continue
        words.append(
            {
                "text": txt,
                "left": int(data["left"][i] / scale),
                "top": int(data["top"][i] / scale),
                "width": int(data["width"][i] / scale),
                "height": int(data["height"][i] / scale),
            }
        )
    return words


def box_for_label(words: list[dict], label: str) -> Optional[tuple[int, int, int, int]]:
    needle = re.sub(r"\s+", " ", label.lower()).strip(" :")
    parts = [p for p in re.split(r"\s+", needle) if len(p) > 2]
    if not parts:
        return None
    for i in range(len(words)):
        chunk = []
        left = words[i]["left"]
        top = words[i]["top"]
        right = left + words[i]["width"]
        bottom = top + words[i]["height"]
        for j in range(i, min(i + max(6, len(parts) + 2), len(words))):
            word = words[j]
            if abs(word["top"] - top) > 25:
                break
            chunk.append(word["text"].lower())
            right = max(right, word["left"] + word["width"])
            bottom = max(bottom, word["top"] + word["height"])
            joined = " ".join(chunk)
            if all(p in joined for p in parts[: min(3, len(parts))]):
                return left, top, right - left, bottom - top
    return None
