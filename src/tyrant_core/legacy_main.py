"""TyrantCore legacy v1 启动入口。"""

from __future__ import annotations

import asyncio

from tyrant_core.legacy.__main__ import main


if __name__ == "__main__":
    asyncio.run(main())
