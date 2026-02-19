from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
