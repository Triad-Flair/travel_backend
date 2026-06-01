from fastapi import HTTPException, status


class AppError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code
        self.message = message


class NotFoundError(AppError):
    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "NOT_FOUND", f"{resource} not found")


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "FORBIDDEN", message)


class ConflictError(AppError):
    def __init__(self, message: str = "Resource already exists") -> None:
        super().__init__(status.HTTP_409_CONFLICT, "CONFLICT", message)


class ValidationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message)


class BadRequestError(AppError):
    def __init__(self, message: str, code: str = "BAD_REQUEST") -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, code, message)


class PaymentError(AppError):
    def __init__(self, message: str, code: str = "PAYMENT_ERROR") -> None:
        super().__init__(status.HTTP_402_PAYMENT_REQUIRED, code, message)


class ServiceUnavailableError(AppError):
    def __init__(self, service: str) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            f"{service} is temporarily unavailable",
        )


class RateLimitError(AppError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "RATE_LIMIT_EXCEEDED",
            "Too many requests. Please try again later.",
        )


class TokenExpiredError(UnauthorizedError):
    def __init__(self) -> None:
        super().__init__("Token has expired")
        self.code = "TOKEN_EXPIRED"


class InvalidTokenError(UnauthorizedError):
    def __init__(self) -> None:
        super().__init__("Invalid token")
        self.code = "INVALID_TOKEN"
