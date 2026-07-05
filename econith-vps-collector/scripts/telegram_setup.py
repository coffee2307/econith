#!/usr/bin/env python3
"""One-shot helper: discover Telegram chat_id after you /start the bot.

Usage on VPS::

    /opt/econith-vps-collector/.venv/bin/python scripts/telegram_setup.py

Then open Telegram -> @econith_vps_bot -> tap Start (/start), and re-run.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: .venv/bin/pip install httpx")
    sys.exit(1)

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
BOT_USERNAME = "econith_vps_bot"


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_API_TOKEN", "").strip()


def main() -> int:
    token = _token()
    if not token:
        print("TELEGRAM_BOT_API_TOKEN missing. Set it in .env first.")
        return 1

    with httpx.Client(timeout=15.0) as client:
        me = client.get(f"https://api.telegram.org/bot{token}/getMe").json()
        if not me.get("ok"):
            print("Invalid bot token:", me)
            return 1
        bot = me["result"]
        print(f"Bot OK: @{bot.get('username')} (id={bot.get('id')})")

        updates = client.get(f"https://api.telegram.org/bot{token}/getUpdates").json()
        if not updates.get("ok"):
            print("getUpdates failed:", updates)
            return 1

        results = updates.get("result") or []
        if not results:
            print()
            print("No messages yet.")
            print(f"1) Open Telegram and search: @{BOT_USERNAME}")
            print("2) Tap START (or send /start)")
            print("3) Re-run this script")
            return 2

        # Latest private chat that messaged the bot.
        chat_id = None
        user_name = ""
        for item in reversed(results):
            msg = item.get("message") or item.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                chat_id = chat["id"]
                user_name = chat.get("first_name") or chat.get("username") or "user"
                break

        if chat_id is None:
            print("Updates found but no chat id parsed:", json.dumps(results[-1], indent=2)[:400])
            return 1

        print(f"Found chat_id={chat_id} ({user_name})")

        # Test send
        test = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "✅ ECONITH VPS alerts configured successfully.",
            },
        ).json()
        if not test.get("ok"):
            print("Test send failed:", test)
            return 1
        print("Test message sent OK.")

        # Patch .env
        if ENV_PATH.exists():
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            seen = False
            for line in lines:
                if line.startswith("TELEGRAM_CHAT_ID="):
                    out.append(f"TELEGRAM_CHAT_ID={chat_id}")
                    seen = True
                else:
                    out.append(line)
            if not seen:
                out.append(f"TELEGRAM_CHAT_ID={chat_id}")
            ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
            print(f"Updated {ENV_PATH}")
        else:
            print(f"Add to .env: TELEGRAM_CHAT_ID={chat_id}")

        print("Restart collector: systemctl restart econith-collector.service")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
