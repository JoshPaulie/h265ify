from pathlib import Path
from unittest.mock import patch, MagicMock
from h265ify.pipeline import find_replace_pairs


def test_find_replace_pairs_empty_stem() -> None:
    console = MagicMock()
    with patch("h265ify.pipeline._iter_files", return_value=[Path("_h265.mp4")]):
        pairs = find_replace_pairs([Path(".")], console)
        assert pairs == []


def test_run_replace_oserror() -> None:
    from h265ify.pipeline import run_replace, ReplacePair

    console = MagicMock()
    pair = ReplacePair(Path("orig.mp4"), Path("orig_h265.mp4"))

    with patch(
        "h265ify.pipeline._delete_user_file", side_effect=OSError("test delete error")
    ):
        replaced, skipped = run_replace(
            [pair], dry_run=False, permanent=False, console=console
        )
        assert replaced == 0
        assert skipped == 1
        console.print.assert_any_call("  [red]error:[/] test delete error")
