import sys
import os
from datetime import datetime
from loguru import logger as _logger
from app.config import PROJECT_ROOT

_print_level = "INFO"


def define_log_level(print_level="INFO", logfile_level="DEBUG", name: str = None):
    """Adjust the log level to above level"""
    global _print_level
    _print_level = print_level

    current_date = datetime.now()
    formatted_date = current_date.strftime("%Y%m%d%H%M%S")
    log_name = f"{name}_{formatted_date}" if name else formatted_date

    _logger.remove()

    # 定义过滤函数：如果 IGNORE_WARNING=1，就跳过 warning
    def filter_func(record):
        if os.getenv("IGNORE_WARNING") == "1" and record["level"].name == "WARNING":
            return False
        return True

    # 控制台输出
    _logger.add(sys.stderr, level=print_level, filter=filter_func)
    # 文件输出（完整日志，不过滤）
    _logger.add(PROJECT_ROOT / f"logs/{log_name}.log", level=logfile_level)

    return _logger


logger = define_log_level()


if __name__ == "__main__":
    logger.info("Starting application")
    logger.debug("Debug message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    try:
        raise ValueError("Test error")
    except Exception as e:
        logger.exception(f"An error occurred: {e}")
