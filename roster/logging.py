import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Callers must log event codes and identifiers only, never raw HR payloads.
        return json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                           "level": record.levelname, "event": record.getMessage()})
