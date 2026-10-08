# Security Policy

## Supported versions

Security fixes are released for the latest release line only, currently
0.4.x. Upgrade to the newest 0.4.x release before reporting, and check whether
the problem is still there.

| Version           | Supported |
|-------------------|-----------|
| Latest 0.4.x      | Yes       |
| 0.3.9 and older   | No        |

A fix ships as a new 0.4.x patch release; earlier releases are not patched.

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
- If it is, a fix in a new 0.4.x patch release, followed by a published GitHub
  security advisory that credits you, unless you prefer not to be named.
- Questions in the advisory thread if the report needs more detail.

Please give the maintainers a reasonable chance to release a fix before
disclosing the problem publicly.
