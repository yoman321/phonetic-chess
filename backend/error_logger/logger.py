import logging
import os
from datetime import date


_ERROR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "error_file",
)


class _DailyErrorFileHandler(logging.Handler):
    def emit(self, record):
        try:
            os.makedirs(_ERROR_DIR, exist_ok=True)
            path = os.path.join(_ERROR_DIR, f"{date.today().isoformat()}.log")
            with open(path, "a") as f:
                f.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


logger = logging.getLogger("phonetic_chess")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    _file_handler = _DailyErrorFileHandler()
    _file_handler.setLevel(logging.ERROR)
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)

    _stream_handler = logging.StreamHandler()
    _stream_handler.setLevel(logging.INFO)
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_stream_handler)

    logger.propagate = False
