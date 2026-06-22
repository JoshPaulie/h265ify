from typing import Any
from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder
import signal


def test_pipeline_sigint_handler() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    registered_handler = None
    original_signal = signal.signal

    def mock_signal(sig: int, handler: Any) -> object:
        nonlocal registered_handler
        if sig == signal.SIGINT:
            registered_handler = handler
        return original_signal(sig, handler)

    with patch("signal.signal", side_effect=mock_signal):
        with patch("h265ify.pipeline.run_encode") as mock_run_encode:

            def mock_run(*args: object, **kwargs: object) -> tuple[bool, list[str]]:
                if callable(registered_handler):
                    registered_handler(signal.SIGINT, None)
                return True, []

            mock_run_encode.side_effect = mock_run

            results, interrupted = run_pipeline([job], enc, 23, False, False, console)
            assert interrupted is True
            assert len(results) == 0


def test_pipeline_sigint_handler_with_tmp_file() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    registered_handler = None
    original_signal = signal.signal

    def mock_signal(sig: int, handler: Any) -> object:
        nonlocal registered_handler
        if sig == signal.SIGINT:
            registered_handler = handler
        return original_signal(sig, handler)

    with patch("signal.signal", side_effect=mock_signal):
        with patch("h265ify.pipeline.run_encode") as mock_run_encode:
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.unlink") as mock_unlink:

                    def mock_run(
                        *args: object, **kwargs: object
                    ) -> tuple[bool, list[str]]:
                        if callable(registered_handler):
                            registered_handler(signal.SIGINT, None)
                        return True, []

                    mock_run_encode.side_effect = mock_run

                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert interrupted is True
                    mock_unlink.assert_called_with(missing_ok=True)
