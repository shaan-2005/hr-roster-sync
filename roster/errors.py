import uuid
import logging
from rest_framework.response import Response
from rest_framework.views import exception_handler

log = logging.getLogger("roster")


def safe_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        trace_id = str(uuid.uuid4())
        log.error("unhandled_error trace_id=%s", trace_id)
        return Response({"code": "internal_error", "message": "Request could not be completed.",
                         "trace_id": trace_id}, status=500)
    code = getattr(exc, "default_code", "request_error")
    if isinstance(code, (list, dict)):
        code = "validation_error"
    response.data = {"code": str(code), "message": "Request could not be completed.",
                     "trace_id": str(uuid.uuid4())}
    return response
