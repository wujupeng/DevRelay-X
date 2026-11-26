import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from windows_gpu_node.config import NodeConfig


def configure_logging(config: NodeConfig) -> None:
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("windows_gpu_node")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        Path(config.log_file),
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
