#!/usr/bin/env sh
# Throwaway Redis for the test suite.
#
# Port 6380, not the standard 6379: the Redis tests delete every
# `fastapi_cachex:`-prefixed key on the server they connect to, which on a
# Redis you already use is your own application's cache. See
# tests/live_servers.py.
docker run --rm -d -p 6380:6379 --name cachex-test-redis redis:alpine

echo "Run the suite with: CACHEX_TEST_REDIS_PORT=6380 uv run pytest"
