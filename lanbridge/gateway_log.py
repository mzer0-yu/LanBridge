"""Bounded gateway transfer diagnostics, separate from externally redirected stderr."""
import logging
from logging.handlers import RotatingFileHandler


def transfer_logger(root):
    logger = logging.Logger("lanbridge.gateway", level=logging.WARNING)
    logger.propagate = False
    handler = RotatingFileHandler(root / "gateway.log", maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger, handler


def configure_runtime_logging(root):
    logger = logging.getLogger("uvicorn.error")
    handler = RotatingFileHandler(root / "runtime.log", maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    return handler
