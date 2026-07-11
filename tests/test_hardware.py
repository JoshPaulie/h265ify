"""Tests for hardware.py - encoder_quality_flags, Encoder dataclass."""

from __future__ import annotations

from h265ify.hardware import Encoder, encoder_quality_flags, pix_fmt_for_encoder


class TestEncoderQualityFlags:
    # -- libx265 --
    def test_libx265_default(self) -> None:
        flags = encoder_quality_flags("libx265", crf=23)
        assert flags == ["-crf", "23", "-preset", "medium"]

    def test_libx265_low_crf(self) -> None:
        flags = encoder_quality_flags("libx265", crf=18)
        assert flags == ["-crf", "18", "-preset", "medium"]

    def test_libx265_high_crf(self) -> None:
        flags = encoder_quality_flags("libx265", crf=28)
        assert flags == ["-crf", "28", "-preset", "medium"]

    # -- VideoToolbox (linear mapping: 20 + (crf/51)*65) --
    def test_videotoolbox_crf_0(self) -> None:
        flags = encoder_quality_flags("hevc_videotoolbox", crf=0)
        assert flags[0] == "-q:v"
        assert flags[1] == "85"

    def test_videotoolbox_crf_23(self) -> None:
        flags = encoder_quality_flags("hevc_videotoolbox", crf=23)
        assert flags[0] == "-q:v"
        # 85 - (23/51)*65 ≈ 55
        assert flags[1] == "55"
        assert flags[2:] == ["-realtime", "0", "-allow_sw", "1"]

    def test_videotoolbox_crf_51(self) -> None:
        flags = encoder_quality_flags("hevc_videotoolbox", crf=51)
        assert flags[0] == "-q:v"
        assert flags[1] == "20"

    # -- NVENC --
    def test_nvenc(self) -> None:
        flags = encoder_quality_flags("hevc_nvenc", crf=23)
        assert flags == ["-rc", "vbr", "-cq", "23", "-preset", "p4"]

    # -- QSV (default medium → slow) --
    def test_qsv(self) -> None:
        flags = encoder_quality_flags("hevc_qsv", crf=20)
        assert flags == [
            "-global_quality",
            "20",
            "-look_ahead",
            "1",
            "-preset",
            "slow",
        ]

    # -- AMF (now uses CQP mode) --
    def test_amf_cqp_quality(self) -> None:
        flags = encoder_quality_flags("hevc_amf", crf=18)
        assert flags == [
            "-rc",
            "cqp",
            "-qp_p",
            "18",
            "-qp_i",
            "16",
            "-quality",
            "quality",
        ]

    def test_amf_cqp_default(self) -> None:
        flags = encoder_quality_flags("hevc_amf", crf=23)
        assert flags == [
            "-rc",
            "cqp",
            "-qp_p",
            "23",
            "-qp_i",
            "21",
            "-quality",
            "quality",
        ]

    def test_amf_cqp_high(self) -> None:
        flags = encoder_quality_flags("hevc_amf", crf=28)
        assert flags == [
            "-rc",
            "cqp",
            "-qp_p",
            "28",
            "-qp_i",
            "26",
            "-quality",
            "quality",
        ]

    def test_amf_crf_0_i_frame_clamped(self) -> None:
        flags = encoder_quality_flags("hevc_amf", crf=0)
        assert flags[3] == "0"  # qp_i clamped to 0
        assert flags[5] == "0"  # qp_p

    # -- Preset mapping (non-default) --
    def test_nvenc_custom_preset(self) -> None:
        flags = encoder_quality_flags("hevc_nvenc", crf=23, preset="veryslow")
        assert flags == ["-rc", "vbr", "-cq", "23", "-preset", "p7"]

    def test_qsv_custom_preset(self) -> None:
        flags = encoder_quality_flags("hevc_qsv", crf=20, preset="fast")
        assert flags[-2:] == ["-preset", "medium"]

    def test_amf_custom_preset(self) -> None:
        flags = encoder_quality_flags("hevc_amf", crf=23, preset="ultrafast")
        # ultrafast → speed
        assert "speed" in flags

    def test_unknown_encoder_returns_empty(self) -> None:
        assert encoder_quality_flags("nonexistent", crf=23) == []


class TestEncoderDataclass:
    def test_fields(self) -> None:
        e = Encoder(name="libx265", is_hardware=False, label="CPU (libx265)")
        assert e.name == "libx265"
        assert not e.is_hardware
        assert e.label == "CPU (libx265)"

    def test_hardware_field(self) -> None:
        e = Encoder(name="hevc_nvenc", is_hardware=True, label="NVENC")
        assert e.is_hardware


class TestPixFmtForEncoder:
    def test_8bit_returns_none(self) -> None:
        assert pix_fmt_for_encoder("libx265", 8) is None
        assert pix_fmt_for_encoder("hevc_videotoolbox", 8) is None
        assert pix_fmt_for_encoder("hevc_nvenc", 8) is None

    def test_10bit_libx265(self) -> None:
        assert pix_fmt_for_encoder("libx265", 10) == "yuv420p10le"

    def test_10bit_videotoolbox(self) -> None:
        assert pix_fmt_for_encoder("hevc_videotoolbox", 10) == "p010le"

    def test_10bit_nvenc(self) -> None:
        assert pix_fmt_for_encoder("hevc_nvenc", 10) == "p010le"

    def test_10bit_qsv(self) -> None:
        assert pix_fmt_for_encoder("hevc_qsv", 10) == "p010le"

    def test_10bit_amf(self) -> None:
        assert pix_fmt_for_encoder("hevc_amf", 10) == "p010le"

    def test_12bit_libx265(self) -> None:
        assert pix_fmt_for_encoder("libx265", 12) == "yuv420p10le"

    def test_unknown_encoder_returns_none(self) -> None:
        assert pix_fmt_for_encoder("nonexistent", 10) is None

    def test_unknown_encoder_8bit(self) -> None:
        assert pix_fmt_for_encoder("nonexistent", 8) is None
