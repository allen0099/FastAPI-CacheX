#!/usr/bin/env sh
# Throwaway Memcached for the test suite.
#
# Port 11212, not the standard 11211: the Memcached tests call flush_all, which
# wipes the whole server. Pointing them at an instance you already use would
# destroy its contents. See tests/live_servers.py.
docker run --rm -d -p 11212:11211 --name cachex-test-memcached memcached:1.6-alpine

echo "Run the suite with: CACHEX_TEST_MEMCACHED_PORT=11212 uv run pytest"
