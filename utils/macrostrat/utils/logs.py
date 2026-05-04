import logging
from contextlib import contextmanager
from sys import stderr

from colorlog import ColoredFormatter, StreamHandler


class SparrowLogFormatter(ColoredFormatter):
    def __init__(self, *args, **kwargs):
        super().__init__(
            "%(log_color)s%(name)s:%(reset)s    %(message)s",
            datefmt=None,
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
            secondary_log_colors={},
            style="%",
        )


console_handler = StreamHandler(stderr)
# create console handler and set level to debug
# console_handler.setLevel(logging.CRITICAL)
# create formatter
# add formatter to ch
console_handler.setFormatter(SparrowLogFormatter())


def get_logger(name=None, level=logging.DEBUG, handler=None):
    log = logging.getLogger(name)
    log.setLevel(level)
    if handler:
        log.addHandler(handler)
    return log


def setup_stderr_logs(*args, level=logging.DEBUG):
    # Customize the root logger so we don't get overridden by uvicorn
    # We may want to customize this further eventually
    # https://github.com/encode/uvicorn/issues/410
    handler = StreamHandler(stderr)
    handler.setFormatter(SparrowLogFormatter())
    handler.setLevel(level)
    for name in args:
        logger = logging.getLogger(name)
        if logger.hasHandlers():
            logger.handlers.clear()
        logger.addHandler(handler)


@contextmanager
def suppress_loggers(*loggers, level=logging.ERROR):
    """Temporarily suppresses logs for a specific logger or the root logger."""
    orig_level_map = {}
    for logger_name in loggers:
        logger = logging.getLogger(logger_name)
        orig_level_map[logger_name] = logger.getEffectiveLevel()
        logger.setLevel(level)
    try:
        yield
    finally:
        for logger_name, original_level in orig_level_map.items():
            logger = logging.getLogger(logger_name)
            logger.setLevel(original_level)
