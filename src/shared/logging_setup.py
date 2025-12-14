#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
from .colored_logging import setup_colored_logging

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        setup_colored_logging(
            level=logging.INFO,
            fmt='%(asctime)s - %(levelname)s - [%(name)s:%(module)s.%(funcName)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    return logger
