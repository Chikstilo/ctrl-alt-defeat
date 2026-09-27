import struct
from dataclasses import dataclass

NPL_SIZE = 15
NPH_SIZE = 10
NPL_SIGNATURE = 0x7E7E

CELL_NAV = 0
CELL_INT_SENSOR = 2
CELL_USI = 8
CELL_CAN = 10
CELL_LLS = 15
CELL_TERMO = 16

CELL_PAYLOAD_LENGTHS = {
    CELL_INT_SENSOR: 26,
    CELL_USI: 6,
    CELL_CAN: 37,
    CELL_LLS: 50,
    CELL_TERMO: 8,
}


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


@dataclass
class NavCell:
    timestamp: int
    longitude: float
    latitude: float
    altitude: int
    speed_avg: float
    course: int
    location_valid: bool
    nsat: int


@dataclass
class NDTPPacket:
    peer_address: int
    service_id: int
    packet_type: int
    nph_request_id: int
    nav: NavCell | None = None

    @property
    def is_handshake(self) -> bool:
        return self.packet_type == 100

    @property
    def is_realtime(self) -> bool:
        return self.packet_type == 101


def parse_npl(data: bytes) -> dict | None:
    if len(data) < NPL_SIZE:
        return None

    signature, data_size, flags, crc_raw, packet_type, peer, request_id = (
        struct.unpack("<HHHHBIH", data[:NPL_SIZE])
    )

    if signature != NPL_SIGNATURE:
        return None

    return {
        "data_size": data_size,
        "flags": flags,
        "crc_raw": crc_raw,
        "type": packet_type,
        "peer_address": peer,
        "request_id": request_id,
    }


def parse_nph(data: bytes) -> dict | None:
    if len(data) < NPH_SIZE:
        return None

    service_id, packet_type, flags, request_id = struct.unpack(
        "<HHHI", data[:NPH_SIZE]
    )

    return {
        "service_id": service_id,
        "type": packet_type,
        "flags": flags,
        "request_id": request_id,
    }


def parse_nav_cell(payload: bytes) -> NavCell | None:
    if len(payload) < 26:
        return None

    (
        timestamp,
        longitude_raw,
        latitude_raw,
        dop_bits,
        _battery_voltage,
        speed_avg,
        _speed_max,
        course,
        _track,
        altitude,
        nsat,
        _pdop,
    ) = struct.unpack("<IIIBBHHHHHBB", payload[:26])

    north = bool((dop_bits >> 5) & 1)
    east = bool((dop_bits >> 6) & 1)
    valid = bool((dop_bits >> 7) & 1)

    latitude = latitude_raw / 10_000_000
    longitude = longitude_raw / 10_000_000

    if not north:
        latitude = -latitude
    if not east:
        longitude = -longitude

    return NavCell(
        timestamp=timestamp,
        longitude=longitude,
        latitude=latitude,
        altitude=altitude,
        speed_avg=float(speed_avg),
        course=course,
        location_valid=valid,
        nsat=nsat,
    )


def parse_cells(body: bytes) -> NavCell | None:
    position = 0
    nav = None

    while position + 2 <= len(body):
        cell_type = body[position]
        position += 2 

        if cell_type == CELL_NAV:
            payload_length = 26
        else:
            payload_length = CELL_PAYLOAD_LENGTHS.get(cell_type)
        if payload_length is None:
            break

        end = position + payload_length
        if end > len(body):
            break

        if cell_type == CELL_NAV and nav is None:
            nav = parse_nav_cell(body[position:end])

        position = end

    return nav


def parse_packet(frame: bytes) -> NDTPPacket | None:
    if len(frame) < NPL_SIZE + NPH_SIZE:
        return None

    npl = parse_npl(frame[:NPL_SIZE])
    if npl is None:
        return None

    expected_frame_size = NPL_SIZE + npl["data_size"]
    if expected_frame_size != len(frame):
        return None

    nph_start = NPL_SIZE
    nph_end = nph_start + NPH_SIZE
    nph = parse_nph(frame[nph_start:nph_end])
    if nph is None:
        return None

    crc_from_packet = (
        ((npl["crc_raw"] & 0xFF) << 8)
        | ((npl["crc_raw"] >> 8) & 0xFF)
    )
    crc_calculated = crc16_modbus(frame[NPL_SIZE:])

    if crc_from_packet != crc_calculated:
        return None

    body = frame[nph_end:]
    nav = parse_cells(body) if nph["type"] == 101 else None

    return NDTPPacket(
        peer_address=npl["peer_address"],
        service_id=nph["service_id"],
        packet_type=nph["type"],
        nph_request_id=nph["request_id"],
        nav=nav,
    )
