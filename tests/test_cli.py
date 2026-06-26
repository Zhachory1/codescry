from repo_index_mcp.cli import positive_int


def test_positive_int() -> None:
    assert positive_int("3") == 3
