"""Telegram alerts. Silent if not configured."""
from __future__ import annotations
import logging
import httpx
from . import settings

log = logging.getLogger(__name__)


def send(text: str) -> None:
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                   json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text[:4000]}, timeout=15)
    except Exception as e:
        log.warning("telegram send failed: %s", e)
