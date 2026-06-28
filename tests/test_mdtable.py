from newsparser._mdtable import split_row, is_separator


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
