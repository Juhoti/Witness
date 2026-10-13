"""The Witness Telegram bot: answers token checks from anyone, and delivers watch alerts.

Long-polls Telegram for messages. In private chats it replies to an address or ticker with
check.check(), and to /watch and /unwatch by adding or removing the chat from the alert list.
Group chats are ignored. Replies are plain text. Message text is never logged or stored; only the
ids of chats that asked for alerts are kept, in state/bot_subscribers.json.

    python -m products.token_check.bot
"""
from __future__ import annotations
import json
import logging
import time
import httpx
from agent import settings
from . import check

log = logging.getLogger("bot")
SUBS = settings.STATE_DIR / "bot_subscribers.json"
API = "https://api.telegram.org/bot{token}/{method}"
MIN_GAP_S = 3.0


def _call(method: str, **params):
    r = httpx.post(API.format(token=settings.TELEGRAM_BOT_TOKEN, method=method), json=params, timeout=70)
    return r.json()


def subscribers() -> set[int]:
    try:
        return set(json.loads(SUBS.read_text()))
    except (OSError, ValueError):
        return set()


def _save(subs: set[int]) -> None:
    SUBS.parent.mkdir(exist_ok=True)
    SUBS.write_text(json.dumps(sorted(subs)))


def send(chat_id: int, text: str) -> bool:
    try:
        return bool(_call("sendMessage", chat_id=chat_id, text=text[:4000], disable_web_page_preview=True).get("ok"))
    except Exception as e:
        log.warning("send failed: %s", settings.redact(str(e))[:80])
        return False


def broadcast(text: str) -> int:
    """Send to every subscriber and to the owner. Returns how many were reached."""
    targets = subscribers()
    if settings.TELEGRAM_CHAT_ID:
        targets.add(int(settings.TELEGRAM_CHAT_ID))
    return sum(send(c, text) for c in sorted(targets))


def handle(update: dict, last_reply: dict) -> None:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private" or not isinstance(msg.get("text"), str):
        return
    cid, text = chat["id"], msg["text"].strip()
    if time.time() - last_reply.get(cid, 0) < MIN_GAP_S:
        return
    last_reply[cid] = time.time()
    cmd = text.split()[0].lower() if text.startswith("/") else ""
    if cmd == "/watch":
        subs = subscribers(); subs.add(cid); _save(subs)
        send(cid, "You'll get de-peg and risky-market alerts from Witness. /unwatch to stop.")
    elif cmd == "/unwatch":
        subs = subscribers(); subs.discard(cid); _save(subs)
        send(cid, "Alerts stopped.")
    elif cmd in ("/start", "/help"):
        send(cid, check.check(""))
    else:
        send(cid, check.check(text))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(settings.LOG_DIR / "bot.log")])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    offset, last_reply, answered = None, {}, 0
    log.info("bot started")
    while True:
        try:
            res = _call("getUpdates", timeout=50, offset=offset, allowed_updates=["message"])
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                handle(u, last_reply)
                answered += 1
                if answered % 50 == 0:
                    log.info("%d messages handled", answered)
        except Exception as e:
            log.warning("poll failed: %s", settings.redact(str(e))[:80])
            time.sleep(5)


if __name__ == "__main__":
    main()
