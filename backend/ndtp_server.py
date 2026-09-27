import asyncio
import logging
import struct
from datetime import datetime, timezone

from cache import get_worker_redis
from config import NDTP_STREAM_KEY, NDTP_TCP_PORT
from database import SessionLocal
from models import TelemetryRecord, VehicleRoute
from ndtp_parser import NPH_SIZE, NPL_SIZE, parse_packet

logger = logging.getLogger("ndtp_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

MAX_DATA_SIZE = 65_535
HEARTBEAT_KEY = "ndtp:heartbeat"
HEARTBEAT_INTERVAL = 5   # секунд
HEARTBEAT_TTL = 10       # секунд (в 2 раза больше интервала)


async def heartbeat_task() -> None:
    """Пишет метку времени в Redis каждые N секунд, чтобы API знал, что сервер жив."""
    while True:
        try:
            redis_client = get_worker_redis()
            redis_client.set(
                HEARTBEAT_KEY,
                datetime.now(timezone.utc).isoformat(),
                ex=HEARTBEAT_TTL,
            )
        except Exception:
            logger.exception("Не удалось записать heartbeat в Redis")
        await asyncio.sleep(HEARTBEAT_INTERVAL)

def save_realtime_packet(packet) -> None:
    """Синхронная работа с БД и Redis; вызывается через asyncio.to_thread."""

    nav = packet.nav
    if nav is None:
        return

    timestamp = nav.timestamp or int(datetime.now(timezone.utc).timestamp())
    event_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)

    db = SessionLocal()
    try:
        mapping = (
            db.query(VehicleRoute)
            .filter(
                VehicleRoute.unit_id == packet.peer_address,
                VehicleRoute.active.is_(True),
            )
            .one_or_none()
        )

        # Незарегистрированный unit не теряем: сохраняем под его числовым ID,
        # но оставляем route_id пустым. Привязку можно добавить через /vehicles/.
        vehicle_id = mapping.vehicle_id if mapping else str(packet.peer_address)
        route_id = mapping.route_id if mapping else None

        record = TelemetryRecord(
            vehicle_id=vehicle_id,
            route_id=route_id,
            lat=nav.latitude,
            lon=nav.longitude,
            alt=nav.altitude,
            speed=nav.speed_avg,
            heading=nav.course,
            location_valid=nav.location_valid,
            event_time=event_time,
        )

        db.add(record)
        db.commit()

        redis_client = get_worker_redis()
        redis_client.xadd(
            NDTP_STREAM_KEY,
            {
                "vehicle_id": vehicle_id,
                "route_id": route_id or "",
                "lat": str(nav.latitude),
                "lon": str(nav.longitude),
                "alt": str(nav.altitude),
                "speed": str(nav.speed_avg),
                "heading": str(nav.course),
                "location_valid": str(int(nav.location_valid)),
                "stop_id": "",
                "dwell_time_seconds": "0",
                "event_time": event_time.isoformat(),
            },
            maxlen=10_000,
            approximate=True,
        )

        logger.info(
            "NDTP unit=%s vehicle=%s route=%s lat=%.6f lon=%.6f",
            packet.peer_address,
            vehicle_id,
            route_id,
            nav.latitude,
            nav.longitude,
        )

    except Exception:
        db.rollback()
        logger.exception(
            "Ошибка сохранения NDTP-пакета unit_id=%s",
            packet.peer_address,
        )
    finally:
        db.close()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    peer = writer.get_extra_info("peername")
    logger.info("NDTP подключение: %s", peer)
    buffer = bytearray()

    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break

            buffer.extend(chunk)

            while True:
                if len(buffer) < NPL_SIZE:
                    break

                signature, data_size = struct.unpack("<HH", buffer[:4])

                if signature != 0x7E7E:
                    # Ищем следующую сигнатуру, чтобы восстановить поток.
                    next_signature = buffer.find(b"\x7e\x7e", 1)
                    if next_signature < 0:
                        buffer.clear()
                    else:
                        del buffer[:next_signature]
                    logger.warning("Потеря синхронизации NDTP от %s", peer)
                    continue

                if data_size < NPH_SIZE or data_size > MAX_DATA_SIZE:
                    logger.warning("Недопустимый размер кадра NDTP: %s", data_size)
                    del buffer[:2]
                    continue

                frame_size = NPL_SIZE + data_size
                if len(buffer) < frame_size:
                    break

                frame = bytes(buffer[:frame_size])
                del buffer[:frame_size]

                packet = parse_packet(frame)
                if packet is None:
                    logger.warning("Некорректный NDTP-кадр от %s", peer)
                    continue

                if packet.is_handshake:
                    # В приложенной спецификации описан формат запроса,
                    # но не описан подтверждённый формат ответа.
                    # Поэтому здесь не отправляем выдуманный ответ.
                    logger.info(
                        "Handshake: unit_id=%s request_id=%s",
                        packet.peer_address,
                        packet.nph_request_id,
                    )
                elif packet.is_realtime:
                    await asyncio.to_thread(save_realtime_packet, packet)

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Ошибка TCP-соединения %s", peer)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("NDTP отключение: %s", peer)


async def main() -> None:
    asyncio.create_task(heartbeat_task())   # <-- добавить эту строку
    
    server = await asyncio.start_server(
        handle_client,
        host="0.0.0.0",
        port=NDTP_TCP_PORT,
    )
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logger.info("NDTP TCP слушает %s", addresses)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
