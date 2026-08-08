"""
PINLIVES Pro v3.1 - Logging setup.

Without this, every logger.info() in the codebase is discarded: the root logger
defaults to WARNING with no handler, so an operator watching the logs sees
nothing while the system runs.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

_configured = False

_FORMAT = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'


def setup_logging(
    level: str = 'INFO',
    log_file: Optional[str] = None,
    component: str = 'pinlives',
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure root logging once per process. Safe to call repeatedly."""
    global _configured

    root = logging.getLogger()
    if _configured:
        return logging.getLogger(component)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Rotate so a long-running listener cannot fill the disk.
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except (OSError, PermissionError) as e:
            # Losing the file sink must not stop the process; stdout still works.
            root.warning("File logging disabled for %s: %s: %s", log_file, type(e).__name__, e)

    # Telethon's per-update chatter drowns out everything else at INFO.
    logging.getLogger('telethon').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)

    _configured = True
    return logging.getLogger(component)
