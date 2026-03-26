"""TyrantCore v2 启动入口。"""

from __future__ import annotations

import asyncio
import sys

from tyrant_core.adapters import CliAdapter
from tyrant_core.runtime import build_embedded_runtime_gateway


async def main() -> None:
    stack = build_embedded_runtime_gateway()
    gateway = stack.gateway
    if sys.stdin.isatty() and sys.stdout.isatty():
        cli = CliAdapter(gateway)
        await cli.run()
        return

    await gateway.start()
    try:
        print("TyrantCore v2 is active.")
        print(
            f"gateway={gateway.__class__.__name__} api={gateway.api.__class__.__name__}"
        )
    finally:
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
