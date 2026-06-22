from unittest.mock import patch
from h265ify import main


def test_main_replace_returns() -> None:
    # mock _cmd_replace to not exit, so main can return
    with patch("sys.argv", ["h265ify", "--replace", "test.mp4"]):
        with patch("h265ify._cmd_replace"):
            main()
            # if it didn't crash or exit, test passes and line 209 is covered
