#!/usr/bin/env python3
"""
Simple command line client into RPC commands
Can be used as an alternate to Telegram

Should not import anything from econith,
so it can be used as a standalone script.
"""

from econith_client.ft_client import main


if __name__ == "__main__":
    main()
