"""Entry point — delegates to the newsletter pipeline package."""

import asyncio
import logging

from core.config import _validate_config
from core.pipeline import process_newsletters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    _validate_config()
    asyncio.run(process_newsletters())
