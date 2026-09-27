"""Фикстуры и хелперы для тестов парсера NDTP."""
import struct
import os

# Переменные для запуска тестов с хоста (Docker пробрасывает порты 5433/6380)
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5433")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6380")
os.environ.setdefault("REDIS_DB", "0")
os.environ.setdefault("ML_SERVICE_URL", "http://localhost:8002")
import pytest

from ndtp_parser import (
    CELL_NAV,
    NPH_SIZE,
    NPL_SIGNATURE,
    NPL_SIZE,
    crc16_modbus,
)


def build_npl(data_size, packet_type, peer_address, crc, request_id=0):
    """Собирает NPL-заголовок (15 байт)."""
    return struct.pack(
        "<HHHHBIH",
        NPL_SIGNATURE, data_size, 0, crc, packet_type, peer_address, request_id,
    )


def build_nph(service_id, packet_type, request_id=0):
    """Собирает NPH-заголовок (10 байт)."""
    return struct.pack("<HHHI", service_id, packet_type, 0, request_id)


def build_nav_cell(
    timestamp=1_700_000_000,
    lat=55.7558,
    lon=37.6173,
    altitude=100,
    speed=30.0,
    course=90,
    valid=True,
    nsat=10,
):
    """Собирает навигационную ячейку (26 байт payload)."""
    lat_raw = int(abs(lat) * 10_000_000)
    lon_raw = int(abs(lon) * 10_000_000)

    # extraDop: bit5 = north, bit6 = east, bit7 = valid
    dop_bits = 0
    if lat >= 0:
        dop_bits |= 1 << 5
    if lon >= 0:
        dop_bits |= 1 << 6
    if valid:
        dop_bits |= 1 << 7

    return struct.pack(
        "<IIIBBHHHHHBB",
        timestamp, lon_raw, lat_raw, dop_bits,
        120, int(speed), int(speed),
        course, 0, altitude, nsat, 100,
    )


def build_realtime_frame(
    peer_address=1166336,
    service_id=0,
    request_id=1,
    nav_payload=None,
):
    """Собирает полный NDTP-кадр с корректным CRC (packet_type=101)."""
    if nav_payload is None:
        nav_payload = build_nav_cell()

    cell = bytes([CELL_NAV, 0]) + nav_payload
    body = cell
    nph = build_nph(service_id=service_id, packet_type=101, request_id=request_id)

    data_size = len(nph) + len(body)
    crc_calculated = crc16_modbus(nph + body)
    crc_swapped = ((crc_calculated & 0xFF) << 8) | ((crc_calculated >> 8) & 0xFF)

    npl = build_npl(
        data_size=data_size,
        packet_type=0x02,
        peer_address=peer_address,
        crc=crc_swapped,
    )
    return npl + nph + body


def build_handshake_frame(peer_address=1166336, request_id=1):
    """Собирает handshake-кадр (packet_type=100)."""
    nph = build_nph(service_id=0, packet_type=100, request_id=request_id)
    data_size = len(nph)

    crc = crc16_modbus(nph)
    crc_swapped = ((crc & 0xFF) << 8) | ((crc >> 8) & 0xFF)

    npl = build_npl(
        data_size=data_size,
        packet_type=0x02,
        peer_address=peer_address,
        crc=crc_swapped,
    )
    return npl + nph


@pytest.fixture
def realtime_frame():
    return build_realtime_frame()


@pytest.fixture
def handshake_frame():
    return build_handshake_frame()