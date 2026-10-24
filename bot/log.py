# 📝 Логування: stdout (видно в дашборді Railway) + файл з ротацією.
# Токен бота вирізається з усіх записів, включно з трейсбеками: URL запитів
# до Telegram API містять /bot<TOKEN>/ і інакше потрапили б у логи.

import logging
import sys
from logging.handlers import RotatingFileHandler


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt, datefmt, secrets):
        super().__init__(fmt, datefmt)
        self._secrets = [s for s in secrets if s]

    def format(self, record):
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def setup_logging(secrets=(), log_file=None):
    formatter = RedactingFormatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        '%Y-%m-%d %H:%M:%S',
        secrets,
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # httpx на рівні INFO логує кожен URL запиту — разом із токеном і щосекундним polling
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
