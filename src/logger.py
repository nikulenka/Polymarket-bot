"""
Модуль структурированного логирования в JSON формате.
Улучшает анализируемость логов и интеграцию с системами мониторинга.
"""

import json
import logging
import logging.handlers
from typing import Any, Optional
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Форматер для вывода логов в JSON"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Добавляем доп. поля если есть
        if hasattr(record, 'context'):
            log_data["context"] = record.context
        
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


class PlainTextFormatter(logging.Formatter):
    """Обычный форматер для консоли"""
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] {record.levelname:8} {record.name}: {record.getMessage()}"


def setup_logging(
    log_file: str = "logs/bot.log",
    level: int = logging.INFO,
    json_format: bool = True
) -> logging.Logger:
    """
    Настраивает логирование для бота.
    
    Args:
        log_file: путь к файлу логов
        level: уровень логирования
        json_format: использовать JSON формат (или текстовый)
    """
    import os
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    
    logger = logging.getLogger("polymarket_bot")
    logger.setLevel(level)
    logger.handlers.clear()  # Очищаем старые handlers
    
    # Логирование в файл
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(level)
    
    if json_format:
        file_handler.setFormatter(JSONFormatter())
    else:
        file_handler.setFormatter(PlainTextFormatter())
    
    logger.addHandler(file_handler)
    
    # Логирование в консоль (текстовый формат)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(PlainTextFormatter())
    logger.addHandler(console_handler)
    
    return logger


def log_with_context(logger: logging.Logger, level: int, message: str, context: dict = None):
    """Логирует сообщение с дополнительным контекстом (для JSON)"""
    record = logging.LogRecord(
        name=logger.name,
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None
    )
    if context:
        record.context = context
    logger.handle(record)
