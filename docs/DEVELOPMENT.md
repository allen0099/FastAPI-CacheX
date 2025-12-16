# Development Guide

This document contains information for developers who want to contribute to FastAPI-CacheX.

## Running Tests

1. Run unit tests:

```bash
pytest
```

2. Run tests with coverage report:

```bash
pytest --cov=fastapi_cachex
```

## Using tox

tox ensures the code works across different Python versions (3.10-3.13).

1. Install all Python versions
2. Run tox:

```bash
tox
```

To run for a specific Python version:

```bash
tox -e py310  # only run for Python 3.10
```

## Using pre-commit

pre-commit helps maintain code quality by running checks before each commit.

1. Install pre-commit:
```bash
uv add --dev pre-commit
```

2. Install the pre-commit hooks:
```bash
pre-commit install
```

3. Run pre-commit manually on all files:
```bash
pre-commit run --all-files
```

The pre-commit hooks will automatically run on `git commit`. If any checks fail, fix the issues and try committing again.

## Type Checking with mypy

We use mypy for static type checking to ensure type safety.

1. Run mypy:
```bash
mypy fastapi_cachex
```

2. Run mypy with strict mode:
```bash
mypy fastapi_cachex --strict
```

### Common mypy Issues

- Make sure all functions have type annotations
- Use `Optional[Type]` for parameters that could be None
- Use `from __future__ import annotations` for forward references
- Add `py.typed` file to make your package mypy compliant
