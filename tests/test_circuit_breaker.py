import sys
from pathlib import Path
import time
from unittest.mock import MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))
from circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError


def test_circuit_breaker_closed_to_open():
    """5 lỗi liên tiếp làm state chuyển CLOSED -> OPEN"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    
    def failing_func():
        raise ValueError("API error")
        
    for _ in range(4):
        with pytest.raises(ValueError):
            cb.call(failing_func)
            
    assert cb.state == CircuitState.CLOSED
    
    with pytest.raises(ValueError):
        cb.call(failing_func)
        
    assert cb.state == CircuitState.OPEN
    assert cb.failure_count == 5


def test_circuit_breaker_open_blocks_call():
    """Khi OPEN và chưa hết timeout thì không gọi function thật"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    cb.state = CircuitState.OPEN
    cb.last_failure_time = time.time()
    
    mock_func = MagicMock()
    
    with pytest.raises(CircuitOpenError):
        cb.call(mock_func)
        
    mock_func.assert_not_called()


def test_circuit_breaker_open_to_half_open():
    """Sau timeout thì chuyển sang HALF_OPEN"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    cb.state = CircuitState.OPEN
    # Set time to be just past the timeout
    cb.last_failure_time = time.time() - 61
    
    state_inside_func = None
    def success_func():
        nonlocal state_inside_func
        state_inside_func = cb.state
        return "success"
        
    result = cb.call(success_func)
    
    # Observe that the state was HALF_OPEN before success
    assert state_inside_func == CircuitState.HALF_OPEN
    assert result == "success"
    # And after success it went to CLOSED
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_half_open_success():
    """HALF_OPEN success thì về CLOSED"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    cb.state = CircuitState.HALF_OPEN
    
    mock_func = MagicMock(return_value="success")
    
    result = cb.call(mock_func)
    
    assert result == "success"
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_half_open_failure():
    """HALF_OPEN fail thì quay lại OPEN"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    cb.state = CircuitState.HALF_OPEN
    cb.failure_count = 0 # Simulate failure_count is 0 to verify it still opens
    
    def failing_func():
        raise ValueError("API error")
        
    with pytest.raises(ValueError):
        cb.call(failing_func)
        
    assert cb.state == CircuitState.OPEN
    # failure_count is set to failure_threshold for better observability
    assert cb.failure_count == 5


def test_circuit_breaker_open_last_failure_time_none():
    """Fail-safe khi OPEN mà last_failure_time bị None"""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    cb.state = CircuitState.OPEN
    cb.last_failure_time = None
    
    mock_func = MagicMock()
    
    with pytest.raises(CircuitOpenError):
        cb.call(mock_func)
        
    mock_func.assert_not_called()
