import csv
import sys
from datetime import datetime

from database import SessionLocal, Base, engine
from models import Schedule


def parse_dt(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Не могу распарсить дату: {value}")

def load(path: str):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    count = errors = 0
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            print(f"📋 Колонки: {reader.fieldnames}")
            for i, row in enumerate(reader, start=1):
                try:
                    stop_id = row.get("tt_action_item_id") or row.get("stop_id")
                    tr_id = row.get("tr_id") or row.get("route_id")
                    sched = row.get("time_begin") or row.get("scheduled_arrival")
                    if not (stop_id and tr_id and sched):
                        continue
                    db.add(Schedule(
                        route_id=str(tr_id),
                        stop_id=str(stop_id),
                        stop_name=row.get("building_address"),
                        scheduled_arrival=parse_dt(sched),
                    ))
                    count += 1
                    if count % 1000 == 0:
                        db.commit()
                        print(f"   Загружено: {count}")
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"   ⚠️ Строка {i}: {e}")
        db.commit()
        print(f"\nЗагружено: {count}\nОшибок: {errors}")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python load_schedule.py path/to/schedule.csv")
        sys.exit(1)
    load(sys.argv[1])