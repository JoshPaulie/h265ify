from h265ify import _valid_resize


def test_valid_resize() -> None:
    assert _valid_resize("720p") is True
    assert _valid_resize("1080P") is True
    assert _valid_resize("4k") is True
    assert _valid_resize("1920x1080") is True
    assert _valid_resize("1920") is True
    assert _valid_resize("1920x") is False
    assert _valid_resize("x1080") is False
    assert _valid_resize("abc") is False
    assert _valid_resize("abcxdef") is False
