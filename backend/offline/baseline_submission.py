import csv
import sys


def main(points_path: str, template_path: str, out_path: str):
    cur_dev = {}
    with open(points_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("sample_id")
            if sid:
                cur_dev[sid] = row.get("cur_dev_s", "0") or "0"

    with open(template_path, "r", encoding="utf-8-sig") as fin, \
         open(out_path, "w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin, delimiter=";")
        writer = csv.writer(fout, delimiter=";")
        writer.writerow(["sample_id", "prediction"])
        for row in reader:
            sid = row["sample_id"]
            pred = cur_dev.get(sid, "0")
            writer.writerow([sid, pred])

    print(f"Готово: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Использование: python baseline_submission.py points.csv sample_submission.csv out.csv")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])