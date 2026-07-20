# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structlog configuration for lpass-wrap CLI tools.

Provides TTY-adaptive logging: human-readable coloured output when writing to
a terminal, JSON when piped or redirected (suitable for log aggregation).

Usage::

    from lpass_wrap._logging import setup_logging
    setup_logging(verbose=1)
    log = structlog.get_logger()
    log.info("item_created", name="Homelab/My Secret")
"""

import logging
import sys

import structlog


def setup_logging(verbose: int = 0) -> None:
    """Configure structlog with TTY-adaptive output and verbosity control.

    Verbosity levels mirror the common ``-v`` / ``-vv`` convention:

    * 0 — WARNING and above
    * 1 — INFO and above  (``-v``)
    * 2+ — DEBUG and above (``-vv`` or more)

    All log output goes to **stderr**, keeping stdout free for data.  When
    stderr is a TTY the output uses ``ConsoleRenderer`` with local
    timestamps; otherwise JSON lines are emitted for log aggregation.

    Args:
        verbose: Number of times ``-v`` was passed on the command line.
    """
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG

    is_tty = sys.stderr.isatty()

    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]

    if is_tty:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
        timestamper = structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False)
    else:
        renderer = structlog.processors.JSONRenderer()
        timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    pre_chain: list[structlog.types.Processor] = [
        timestamper,
        *shared_processors,
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=pre_chain + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processor=renderer,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
