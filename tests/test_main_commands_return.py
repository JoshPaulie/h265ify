from unittest.mock import patch
from h265ify import main


def test_main_replace_returns() -> None:
    # mock cmd_replace to not exit, so main can return
    with patch("sys.argv", ["h265ify", "--replace", "test.mp4"]):
        with patch("h265ify.cmd_replace"):
            main()
            # if it didn't crash or exit, test passes and line 209 is covered
