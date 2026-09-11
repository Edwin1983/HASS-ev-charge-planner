"""
logger.py

Centrale logger voor EV Smart Charging Planner.
"""

from .config import DEBUG, LOG_PREFIX


class Logger:

    def __init__(
        self,
        debug=None,
        info=None,
        warning=None,
        error=None,
    ):
        self._debug = debug
        self._info = info
        self._warning = warning
        self._error = error

    def debug(self, text):

        if DEBUG and self._debug is not None:
            self._debug(
                f"{LOG_PREFIX} {text}"
            )

    def info(self, text):

        if self._info is not None:
            self._info(
                f"{LOG_PREFIX} {text}"
            )

    def warning(self, text):

        if self._warning is not None:
            self._warning(
                f"{LOG_PREFIX} WARNING: {text}"
            )

    def error(self, text):

        if self._error is not None:
            self._error(
                f"{LOG_PREFIX} ERROR: {text}"
            )