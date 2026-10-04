"""Fork simulation entry point. Must print one JSON object to stdout and exit 0 on success.
Runs against an Anvil fork of 4663; never against mainnet with a key."""
import json, sys

def simulate(rpc_url: str) -> dict:
    return {"ok": True, "note": "template; replace with real measurements"}

if __name__ == "__main__":
    print(json.dumps(simulate(sys.argv[1] if len(sys.argv) > 1 else "")))
