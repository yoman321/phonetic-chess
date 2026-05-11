"""Exception type raised by controller_operations on failed requests.

Controllers register a single handler that turns these into a JSON
error response, so operation functions can stay focused on the happy
path and raise on any error condition.
"""


class ApiError(Exception):
    """Raised by an operation when a request can't be fulfilled.

    code: short machine-readable error string (e.g. "not_found").
    status: HTTP status code the controller should return.
    extra: additional fields to merge into the JSON error body.
    """

    def __init__(self, code, status, **extra):
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra

    def to_payload(self):
        return {"error": self.code, **self.extra}
