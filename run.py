"""User-friendly entrypoint.

`python run.py` -> launches the bot.
"""

from __future__ import annotations

import asyncio
import sys

from main import amain


def main() -> int:
    try:
        asyncio.run(amain())
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted — exiting.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
