"""Load .env and config/chain.toml. Nothing here signs anything."""
from __future__ import annotations
import os, tomllib
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config" / "chain.toml", "rb") as f:
    CHAIN = tomllib.load(f)

RPC_URL = os.getenv("RPC_URL") or CHAIN["chain"]["rpc"]
RPC_URL_FALLBACK = os.getenv("RPC_URL_FALLBACK") or CHAIN["chain"]["rpc"]
# Bulk eth_getLogs goes to a separate endpoint: keyed providers cap log ranges (Alchemy free tier: 10
# blocks) while the public RPC allows ~1,000+ blocks per query. Defaults to the fallback (public) RPC.
RPC_URL_LOGS = os.getenv("RPC_URL_LOGS") or RPC_URL_FALLBACK
BLOCKSCOUT_API = os.getenv("BLOCKSCOUT_API", CHAIN["chain"]["explorer"] + "/api/v2")
BLOCKSCOUT_API_KEY = os.getenv("BLOCKSCOUT_API_KEY", "")  # sent as x-api-key; unkeyed requests get a Cloudflare 403
SCAN_EVERY_MINUTES = int(os.getenv("SCAN_EVERY_MINUTES", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROPOSER_ENABLED = os.getenv("PROPOSER_ENABLED", "false").lower() == "true"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LEDGER_GIT_REMOTE = os.getenv("LEDGER_GIT_REMOTE", "")

LEDGER_DIR = ROOT / "ledger"
SCORECARD_DIR = LEDGER_DIR / "scorecards"
ENTRY_DIR = LEDGER_DIR / "entries"
POLICY_PATH = ROOT / "config" / "policy.json"
RUNGS_PATH = ROOT / "config" / "rungs.yaml"
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"   # scanner cursors and registries; derived, rebuilt from the chain if lost

def unverified(section: str, key: str) -> str | None:
    """Return the configured address, or None if blank. Callers must treat None as not-yet-verified."""
    v = CHAIN.get(section, {}).get(key, "")
    return v or None


def redact(text: str) -> str:
    """Replace keyed RPC URLs with their host. Apply to every error before it reaches a log or the ledger."""
    from urllib.parse import urlparse
    for url in (RPC_URL, RPC_URL_FALLBACK, RPC_URL_LOGS):
        if url and urlparse(url).path.strip("/"):
            text = text.replace(url, f"{urlparse(url).scheme}://{urlparse(url).hostname}/<key>")
    for secret in (TELEGRAM_BOT_TOKEN, BLOCKSCOUT_API_KEY, ANTHROPIC_API_KEY):
        if secret:
            text = text.replace(secret, "<key>")
    return text
