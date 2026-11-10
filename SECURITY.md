# Security Policy

## Supported versions

Security fixes are released for the latest release line only. 0.5.x is the
latest released line, but releases come only from `master`, which now carries
0.6.0: no further 0.5.x release is possible, so a fix ships in 0.6.0. Upgrade
to the newest release before reporting, and check whether the problem is
still there.

| Version           | Supported                       |
|-------------------|---------------------------------|
| 0.5.3             | Until 0.6.0 is released         |
| 0.5.2 and older   | No                              |

A fix ships in the next release from `master`; earlier releases are not
patched. To prepare for 0.6.0, see
[Migrating to 0.6.0](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_6/).

## Supported Python versions

A Python version is dropped in the first minor release after its end-of-life
date, and the release line before that one warns at import time on the
version about to be dropped. 0.6.0 requires Python 3.11 or later; Python 3.10
reached its end of life in October 2026. Python 3.11 follows in the first
minor release after October 2027.

## Reporting a vulnerability

Please do **not** open a public issue, pull request or discussion for a
security problem. Report it privately through GitHub's private vulnerability
reporting instead:

<https://github.com/allen0099/FastAPI-CacheX/security/advisories/new>

The report is visible only to you and the maintainers until an advisory is
published.

Examples of what counts: a way to read or poison another client's cached
response, to make `@cache` store or serve a response it should have bypassed,
or to make the library leak data it was given to protect. A bug in your own application's
use of the library, or in a dependency that FastAPI-CacheX does not work around,
is usually better reported to that project.

## What to include

The more of these a report has, the faster it can be confirmed:

- The FastAPI-CacheX version, the Python version, and the backend in use
  (memory, Redis or Memcached) with its server version.
- The affected component, for example `@cache`, `CacheManager` or
  `CacheLock`, and the relevant configuration.
- Steps to reproduce, ideally a minimal FastAPI app or test case.
- What an attacker can achieve, and under which conditions.
- Any fix or mitigation you have in mind.

## What to expect

FastAPI-CacheX is maintained by volunteers in their spare time, so there are no
guaranteed response times. What you can expect:

- An acknowledgement once a maintainer has read the report, and a follow-up
  after it has been looked into, saying whether it is being treated as a
  vulnerability.
- If it is, a fix in the next release, followed by a published GitHub
  security advisory that credits you, unless you prefer not to be named.
- Questions in the advisory thread if the report needs more detail.

Please give the maintainers a reasonable chance to release a fix before
disclosing the problem publicly.
