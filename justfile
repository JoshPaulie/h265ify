# Aliases
alias c := check
alias f := format
alias l := lint
alias m := mypy
alias t := test

# Run all quality checks: formatting, linting, type checking, and short tests
check: format lint mypy test

# Run ruff formatter
format:
    uv run ruff format src

# Run ruff linter with auto-fixes
lint:
    uv run ruff check --fix --unsafe-fixes src

# Run mypy type checker
mypy:
    uv run mypy src

# Run tests
test:
    uv run pytest tests/ -v

# Reset test video dir
reset:
    rm -fr test_videos
    cp -r test_videos_base test_videos

