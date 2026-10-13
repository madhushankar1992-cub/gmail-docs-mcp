
from google.auth.exceptions import RefreshError, TransportError

from gmail_docs_mcp import errors
from tests.conftest import make_http_error


def code(exc):
    return errors.map_exception(exc).code


def test_mapping_table():
    assert code(RefreshError("invalid_grant")) == errors.AUTH_REQUIRED
    assert code(make_http_error(401)) == errors.AUTH_REQUIRED
    assert code(make_http_error(403)) == errors.PERMISSION_DENIED
    assert code(make_http_error(403, reason="rateLimitExceeded")) == errors.RATE_LIMITED
    assert code(make_http_error(403, reason="userRateLimitExceeded")) == errors.RATE_LIMITED
    assert code(make_http_error(404)) == errors.NOT_FOUND
    assert code(make_http_error(429)) == errors.RATE_LIMITED
    assert code(make_http_error(400)) == errors.INVALID_INPUT
    assert code(make_http_error(500)) == errors.UPSTREAM_ERROR
    assert code(TimeoutError()) == errors.UPSTREAM_ERROR
    assert code(TransportError("dns")) == errors.UPSTREAM_ERROR
    assert code(RuntimeError("bug")) == errors.UPSTREAM_ERROR


def test_tool_failure_passes_through():
    failure = errors.ToolFailure(errors.NOT_FOUND, "x")
    assert errors.map_exception(failure) is failure
