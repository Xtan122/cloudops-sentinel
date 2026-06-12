import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and call should be blocked."""


class CircuitBreaker:
    """
    Circuit breaker cho external API calls theo REQ-13.6.

    - CLOSED: gọi API bình thường
    - OPEN: chặn call sau 5 lỗi liên tiếp
    - HALF_OPEN: cho thử lại sau recovery_timeout
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = None

    def call(self, func, *args, **kwargs):
        """Wrap external API call với circuit breaker."""
        if self.state == CircuitState.OPEN:
            # Kiểm tra thời gian hiện tại so với last_failure_time
            # Nếu chưa qua recovery_timeout (hoặc bị None) -> chặn call
            if self.last_failure_time is None or time.time() - self.last_failure_time < self.recovery_timeout:
                raise CircuitOpenError("Circuit is OPEN")
            else:
                self.state = CircuitState.HALF_OPEN

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        """Reset circuit sau khi external call thành công."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = None

    def _on_failure(self):
        """Tăng failure counter và mở circuit nếu vượt threshold."""
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.failure_count = self.failure_threshold
            self.last_failure_time = time.time()
            return

        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
