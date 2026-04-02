"""Tests for the token bucket rate limiter."""

from __future__ import annotations

import time

from lg_orch.rate_limit import RateLimiter, TokenBucket


class TestTokenBucket:
    def test_initial_capacity(self) -> None:
        b = TokenBucket(capacity=5.0, refill_rate=1.0)
        # Should be able to acquire up to capacity
        for _ in range(5):
            assert b.acquire() is True
        assert b.acquire() is False

    def test_refill(self) -> None:
        b = TokenBucket(capacity=2.0, refill_rate=10000.0)  # very fast refill
        assert b.acquire() is True
        assert b.acquire() is True
        assert b.acquire() is False
        # After a generous sleep, tokens should refill
        time.sleep(0.05)
        assert b.acquire() is True

    def test_capacity_not_exceeded(self) -> None:
        b = TokenBucket(capacity=3.0, refill_rate=1000.0)
        time.sleep(0.01)  # let it refill
        # Should not exceed capacity
        count = 0
        for _ in range(10):
            if b.acquire():
                count += 1
        assert count <= 4  # capacity + maybe 1 from refill during loop

    def test_zero_rate(self) -> None:
        b = TokenBucket(capacity=1.0, refill_rate=0.0)
        assert b.acquire() is True
        assert b.acquire() is False
        time.sleep(0.01)
        assert b.acquire() is False  # no refill


class TestRateLimiter:
    def test_new_client_gets_capacity(self) -> None:
        rl = RateLimiter(capacity=3.0, refill_rate=0.0)
        for _ in range(3):
            assert rl.check("client-a") is True
        assert rl.check("client-a") is False

    def test_independent_clients(self) -> None:
        rl = RateLimiter(capacity=2.0, refill_rate=0.0)
        assert rl.check("client-a") is True
        assert rl.check("client-a") is True
        assert rl.check("client-a") is False
        # client-b should still have tokens
        assert rl.check("client-b") is True
        assert rl.check("client-b") is True
        assert rl.check("client-b") is False

    def test_cleanup_removes_stale_buckets(self) -> None:
        rl = RateLimiter(capacity=10.0, refill_rate=1.0)
        rl.check("active")
        rl.check("stale")
        # Force stale bucket to look old
        with rl._lock:
            rl._buckets["stale"]._last_refill = time.monotonic() - 7200
        removed = rl.cleanup(max_idle_seconds=3600.0)
        assert removed == 1
        assert "stale" not in rl._buckets
        assert "active" in rl._buckets

    def test_cleanup_no_stale(self) -> None:
        rl = RateLimiter(capacity=10.0, refill_rate=1.0)
        rl.check("client-a")
        removed = rl.cleanup(max_idle_seconds=3600.0)
        assert removed == 0

    def test_default_params(self) -> None:
        rl = RateLimiter()
        # Should allow at least 60 requests for a new client
        for _ in range(60):
            assert rl.check("test") is True
