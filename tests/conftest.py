import os


def pytest_runtest_setup(item):  # type: ignore[no-untyped-def]
    os.environ.setdefault("CODESCRY_EMBEDDING_PROVIDER", "hash")
