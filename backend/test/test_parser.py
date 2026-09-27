"""Тесты NDTP-парсера."""
import struct

import pytest

from ndtp_parser import NPH_SIZE, NPL_SIZE, parse_packet
from tests.conftest import (
    build_handshake_frame,
    build_nav_cell,
    build_realtime_frame,
)


class TestValidFrames:
    """Корректные кадры разбираются без ошибок."""

    def test_realtime_frame_with_nav(self):
        frame = build_realtime_frame(peer_address=1166336)
        packet = parse_packet(frame)

        assert packet is not None
        assert packet.peer_address == 1166336
        assert packet.is_realtime is True
        assert packet.is_handshake is False
        assert packet.nav is not None
        assert 55.0 < packet.nav.latitude < 56.0     # Москва
        assert 37.0 < packet.nav.longitude < 38.0
        assert packet.nav.location_valid is True

    def test_handshake_frame(self):
        frame = build_handshake_frame(peer_address=1166336)
        packet = parse_packet(frame)

        assert packet is not None
        assert packet.peer_address == 1166336
        assert packet.is_handshake is True
        assert packet.nav is None

    def test_negative_coordinates(self):
        """Южное полушарие — широта отрицательная."""
        nav = build_nav_cell(lat=-33.8688, lon=151.2093)   # Сидней
        frame = build_realtime_frame(nav_payload=nav)
        packet = parse_packet(frame)

        assert packet is not None
        assert packet.nav is not None
        assert -34.0 < packet.nav.latitude < -33.0
        assert 151.0 < packet.nav.longitude < 152.0

    def test_invalid_location_flag(self):
        nav = build_nav_cell(valid=False)
        frame = build_realtime_frame(nav_payload=nav)
        packet = parse_packet(frame)

        assert packet is not None
        assert packet.nav is not None
        assert packet.nav.location_valid is False


class TestBrokenFrames:
    """Повреждённые кадры должны отбрасываться."""

    def test_broken_crc(self):
        frame = bytearray(build_realtime_frame())
        frame[6] ^= 0xFF          # портим CRC в NPL
        frame[7] ^= 0xFF

        packet = parse_packet(bytes(frame))
        assert packet is None

    def test_broken_signature(self):
        frame = bytearray(build_realtime_frame())
        frame[0] = 0x00           # ломаем 0x7E7E

        packet = parse_packet(bytes(frame))
        assert packet is None

    def test_too_short_for_npl(self):
        packet = parse_packet(b"\x7e\x7e\x00")
        assert packet is None

    def test_too_short_for_full_frame(self):
        frame = build_realtime_frame()
        truncated = frame[: NPL_SIZE + 5]
        packet = parse_packet(truncated)
        assert packet is None

    def test_data_size_mismatch(self):
        frame = bytearray(build_realtime_frame())
        frame[2:4] = struct.pack("<H", 9999)
        packet = parse_packet(bytes(frame))
        assert packet is None


class TestBoundaryValues:
    """Граничные значения."""

    def test_zero_speed(self):
        nav = build_nav_cell(speed=0.0)
        frame = build_realtime_frame(nav_payload=nav)
        packet = parse_packet(frame)
        assert packet is not None
        assert packet.nav is not None
        assert packet.nav.speed_avg == 0.0

    def test_max_course(self):
        nav = build_nav_cell(course=359)
        frame = build_realtime_frame(nav_payload=nav)
        packet = parse_packet(frame)
        assert packet is not None
        assert packet.nav is not None
        assert packet.nav.course == 359

    def test_equator_and_prime_meridian(self):
        """Точка (0, 0) — граница смены знаков."""
        nav = build_nav_cell(lat=0.0, lon=0.0)
        frame = build_realtime_frame(nav_payload=nav)
        packet = parse_packet(frame)
        assert packet is not None
        assert packet.nav is not None
        assert abs(packet.nav.latitude) < 0.001
        assert abs(packet.nav.longitude) < 0.001