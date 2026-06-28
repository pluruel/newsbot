from newsparser._mdtable import split_row, is_separator, parse_rows


def test_split_row_strips_outer_pipes_and_cells():
    assert split_row("| a | b | c |") == ["a", "b", "c"]


def test_split_row_preserves_empty_cells():
    assert split_row("| a |  | c |") == ["a", "", "c"]


def test_split_row_without_outer_pipes():
    assert split_row("a | b") == ["a", "b"]


def test_is_separator_true_for_dashes_and_colons():
    assert is_separator(["---", ":--", "--:"]) is True


def test_is_separator_false_for_content():
    assert is_separator(["a", "b"]) is False


def test_is_separator_false_for_empty_cell():
    assert is_separator(["---", ""]) is False


def test_parse_rows_maps_lowercased_headers_to_cells():
    text = "| Name | RSS URL |\n|------|--------|\n| Foo | http://x |\n"
    assert parse_rows(text) == [{"name": "Foo", "rss url": "http://x"}]


def test_parse_rows_skips_non_pipe_lines_and_separator():
    text = "# title\n\nsome prose\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    assert parse_rows(text) == [{"a": "1", "b": "2"}]


def test_parse_rows_pads_short_rows():
    text = "| A | B | C |\n|---|---|---|\n| 1 | 2 |\n"
    assert parse_rows(text) == [{"a": "1", "b": "2", "c": ""}]


def test_parse_rows_empty_without_table():
    assert parse_rows("no table here\n") == []
