from h265ify import valid_resize


def test_valid_resize() -> None:
    assert valid_resize("720p") is True
    assert valid_resize("1080P") is True
    assert valid_resize("4k") is True
    assert valid_resize("1920x1080") is True
    assert valid_resize("1920") is True
    assert valid_resize("1920x") is False
    assert valid_resize("x1080") is False
    assert valid_resize("abc") is False
    assert valid_resize("abcxdef") is False
