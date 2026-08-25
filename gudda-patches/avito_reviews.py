# -*- coding: utf-8 -*-
"""Новые отзывы Авито → уведомление в общий чат MAX (все кабинеты)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from gudda.config import settings
from gudda.constants import CODE_NAMES
from gudda.db.models import AvitoChatItemMap, Direction
from gudda.services import avito_messenger_client as avito_api
from gudda.services.avito_accounts import AvitoAccount, list_avito_accounts
from gudda.services.avito_chats import resolve_store_code
from gudda.services.avito_item_tt_map import ensure_item_store_codes
from gudda.services.avito_messenger_client import AvitoMessengerError

logger = logging.getLogger(__name__)


def _state_path(account_key: str) -> Path:
    return settings.upload_dir / f".avito_reviews_state_{account_key}.json"


def _load_state(account_key: str) -> dict[str, Any]:
    path = _state_path(account_key)
    if not path.is_file():
        return {"seen_ids": [], "bootstrapped": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        logger.exception("Failed to read reviews state %s", account_key)
    return {"seen_ids": [], "bootstrapped": False}


def _save_state(account_key: str, state: dict[str, Any]) -> None:
    path = _state_path(account_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_reviews(*, account: AvitoAccount, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    q = f"offset={max(0, offset)}&limit={max(1, min(50, limit))}"
    url = f"https://api.avito.ru/ratings/v1/reviews?{q}"
    payload = avito_api._request_json("GET", url, account=account)  # noqa: SLF001
    if not isinstance(payload, dict):
        raise AvitoMessengerError("Неожиданный ответ Ratings API")
    return payload


def _review_item_fields(rev: dict[str, Any]) -> tuple[str | None, str | None]:
    item = (rev.get("item") or {}) if isinstance(rev.get("item"), dict) else {}
    raw_id = item.get("id")
    item_id = None
    if raw_id is not None and str(raw_id).strip() not in {"", "0", "None"}:
        item_id = str(raw_id).strip()
    title = str(item.get("title") or "").strip() or None
    return item_id, title


def _resolve_review_store(
    db,
    *,
    direction: Direction,
    item_id: str | None,
    item_title: str | None,
) -> tuple[str | None, str | None]:
    """Возвращает (store_code, address) по карте объявлений / on-demand Items API."""
    store_code: str | None = None
    address_note: str | None = None

    if item_id:
        row = db.scalar(
            select(AvitoChatItemMap).where(
                AvitoChatItemMap.direction == direction,
                AvitoChatItemMap.item_id == item_id,
            )
        )
        if row and row.store_code:
            store_code = row.store_code
            address_note = (row.note or "").strip() or None

    if not store_code:
        store_code = resolve_store_code(
            db,
            direction=direction,
            item_id=item_id,
            item_title=item_title,
            item_location=None,
            address_hint=None,
        )

    if not store_code and item_id:
        try:
            found = ensure_item_store_codes(db, direction=direction, item_ids=[item_id])
            store_code = found.get(item_id)
            if store_code:
                db.expire_all()
                row = db.scalar(
                    select(AvitoChatItemMap).where(
                        AvitoChatItemMap.direction == direction,
                        AvitoChatItemMap.item_id == item_id,
                    )
                )
                if row:
                    address_note = (row.note or "").strip() or None
        except Exception:
            logger.exception("ensure_item_store_codes failed for review item=%s", item_id)

    if not store_code:
        return None, None

    addr = CODE_NAMES.get(store_code, "") or address_note or None
    return store_code, addr


def _format_review(
    rev: dict[str, Any],
    *,
    account_label: str,
    store_code: str | None = None,
    store_address: str | None = None,
) -> str:
    score = rev.get("score")
    stars = "★" * int(score or 0) + "☆" * max(0, 5 - int(score or 0))
    sender = ((rev.get("sender") or {}) if isinstance(rev.get("sender"), dict) else {}).get("name") or "Покупатель"
    item = (rev.get("item") or {}) if isinstance(rev.get("item"), dict) else {}
    title = str(item.get("title") or "Товар").strip()
    text = str(rev.get("text") or "").strip().replace("\n", " ")
    if len(text) > 280:
        text = text[:277] + "…"

    lines = [
        f"⭐ Новый отзыв · **{account_label}** {stars} ({score}/5)",
    ]
    if store_code:
        addr = (store_address or CODE_NAMES.get(store_code, "") or "").strip()
        head = f"▶ **{store_code}**"
        if addr:
            head += f" · {addr}"
        lines.append(head)
    else:
        lines.append("▶ ТТ не определена")
    lines.append(f"От: {sender}")
    lines.append(f"Товар: {title}")
    if text:
        lines.append(f"«{text}»")
    return "\n".join(lines)


def _sync_account_reviews(acc: AvitoAccount, *, notify: bool, limit: int, chat_id: str) -> dict[str, Any]:
    try:
        payload = fetch_reviews(account=acc, offset=0, limit=limit)
    except Exception as exc:
        logger.exception("Avito reviews fetch failed [%s]", acc.key)
        return {"ok": False, "account": acc.key, "detail": str(exc), "new": 0, "notified": 0}

    reviews = [r for r in (payload.get("reviews") or []) if isinstance(r, dict)]
    state = _load_state(acc.key)
    seen = {str(x) for x in (state.get("seen_ids") or [])}
    bootstrapped = bool(state.get("bootstrapped"))
    ids_now = [str(r.get("id")) for r in reviews if r.get("id") is not None]
    fresh = [r for r in reviews if str(r.get("id")) not in seen]

    if not bootstrapped:
        for rid in ids_now:
            seen.add(rid)
        state["seen_ids"] = list(seen)[-400:]
        state["bootstrapped"] = True
        _save_state(acc.key, state)
        return {"ok": True, "account": acc.key, "bootstrapped": True, "new": 0, "notified": 0}

    sent = 0
    if notify and fresh and chat_id:
        from gudda.db.session import SessionLocal
        from gudda.services.max_notify import _send_max_message

        direction = Direction.ELECTRONICS
        with SessionLocal() as db:
            for rev in reversed(fresh[:10]):
                item_id, item_title = _review_item_fields(rev)
                store_code, store_address = _resolve_review_store(
                    db,
                    direction=direction,
                    item_id=item_id,
                    item_title=item_title,
                )
                text = _format_review(
                    rev,
                    account_label=acc.label,
                    store_code=store_code,
                    store_address=store_address,
                )
                if _send_max_message(chat_id=chat_id, text=text, format="markdown"):
                    sent += 1
                    logger.info(
                        "Avito review notified id=%s account=%s store=%s item=%s",
                        rev.get("id"),
                        acc.key,
                        store_code or "—",
                        item_id or "—",
                    )
                seen.add(str(rev.get("id")))

    for rid in ids_now:
        seen.add(rid)
    state["seen_ids"] = list(seen)[-400:]
    state["bootstrapped"] = True
    _save_state(acc.key, state)
    return {
        "ok": True,
        "account": acc.key,
        "label": acc.label,
        "new": len(fresh),
        "notified": sent,
        "total": payload.get("total"),
    }


def sync_new_reviews(*, notify: bool = True, limit: int = 20) -> dict[str, Any]:
    accounts = list_avito_accounts()
    if not accounts:
        return {"ok": False, "detail": "avito not configured", "new": 0}

    chat_id = (
        settings.max_reviews_chat_id
        or settings.max_daily_report_chat_id
        or settings.max_bot_test_chat_id
        or ""
    ).strip()
    if notify and (not chat_id or not settings.max_bot_enabled):
        logger.warning("MAX reviews chat not configured — skip review notify")
        notify = False

    results = [_sync_account_reviews(acc, notify=notify, limit=limit, chat_id=chat_id) for acc in accounts]
    total_new = sum(int(r.get("new") or 0) for r in results)
    total_notified = sum(int(r.get("notified") or 0) for r in results)
    return {
        "ok": any(r.get("ok") for r in results),
        "accounts": results,
        "new": total_new,
        "notified": total_notified,
    }
