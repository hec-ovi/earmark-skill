"""Closed error set. Codes match contracts/envelope.schema.json; exit codes match the CLI contract."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any

EXIT_CODES = {
    "invalid_request": 1,
    "forbidden": 1,
    "identity_required": 1,
    "not_joined": 1,
    "no_board": 1,
    "internal_error": 1,
    "not_found": 2,
    "conflict": 3,
    "empty": 4,
    "busy": 5,
}

HTTP_STATUS = {
    "invalid_request": 400,
    "identity_required": 400,
    "not_joined": 400,
    "forbidden": 403,
    "not_found": 404,
    "no_board": 404,
    "conflict": 409,
    "empty": 404,
    "busy": 503,
    "internal_error": 500,
}


class EarmarkError(Exception):
    """Base error. `hint` is the next command to try; `details` is code-specific context."""

    code = "internal_error"
    retriable = False

    def __init__(self, message: str, *, hint: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retriable": self.retriable,
            "hint": self.hint,
            "details": self.details,
        }


class InvalidRequest(EarmarkError):
    code = "invalid_request"


class PayloadTooLarge(InvalidRequest):
    """A file over the attachment limit: invalid_request in the envelope, HTTP 413."""

    @property
    def http_status(self) -> int:
        return 413


class NotFound(EarmarkError):
    code = "not_found"


class Conflict(EarmarkError):
    code = "conflict"


class Empty(EarmarkError):
    """Nothing to do: `next` found no candidate, `wait` timed out. Not a failure."""

    code = "empty"


class Busy(EarmarkError):
    code = "busy"
    retriable = True


class Forbidden(EarmarkError):
    code = "forbidden"


class IdentityRequired(EarmarkError):
    code = "identity_required"


class NotJoined(EarmarkError):
    code = "not_joined"


class NoBoard(EarmarkError):
    code = "no_board"


class InternalError(EarmarkError):
    code = "internal_error"
