rem Throwaway Memcached for the test suite.
rem
rem Port 11212, not the standard 11211: the Memcached tests call flush_all,
rem which wipes the whole server. Pointing them at an instance you already use
rem would destroy its contents. See tests/live_servers.py.
rem
rem Download from https://github.com/jefyt/memcached-windows/releases/tag/1.6.8_mingw

memcached -p 11212 -m 512 -vvv

rem Run the suite with: set CACHEX_TEST_MEMCACHED_PORT=11212 && uv run pytest
