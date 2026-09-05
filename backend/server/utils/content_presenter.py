from fastapi import HTTPException

from yuxi.content.control.errors import ContentApplicationError


def present_content_error(error: ContentApplicationError) -> HTTPException:
    status_code = {"not_found": 404, "conflict": 409, "invalid": 422}[error.kind]
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": error.code, "message": error.message}},
    )
