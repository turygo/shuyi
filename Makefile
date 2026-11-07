.PHONY: check test acceptance
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run python scripts/check_architecture.py
	uv run python -m unittest discover -s tests

test:
	uv run python -m unittest discover -s tests

acceptance:
	SHUYI_LIVE_ACCEPTANCE=1 uv run python -m unittest tests.test_live_acceptance -v
