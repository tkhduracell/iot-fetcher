import logging
import os
import re


class CleanLogs(logging.Filter):
    pattern: re.Pattern = re.compile(r' - - \[.+?] "')

    def filter(self, record: logging.LogRecord) -> bool:
        if "/influx/api/v2/query" in record.msg:
            return False
        record.name = (
            record.name.replace("werkzeug", "http")
                       .replace("root", os.path.basename(__file__))
                       .replace(".py", "")
        )
        record.msg = self.pattern.sub(' - "', record.msg)
        return True


def setup_logging():
    """Configure logging for the application"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s [%(name)s] %(message)s'
    )

    # Requests logging
    wlog = logging.getLogger('werkzeug')
    wlog.setLevel(logging.WARNING)
    wlog.addFilter(CleanLogs())

    rlog = logging.getLogger('root')
    rlog.setLevel(logging.INFO)
    rlog.addFilter(CleanLogs())
