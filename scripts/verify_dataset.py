import csv
import re
from collections import Counter

rows = list(csv.DictReader(open("groww-reviews.csv", encoding="utf-8")))
print("rows:", len(rows), "| distinct texts:", len(set(r["Review"] for r in rows)))
print("avg rating:", round(sum(int(r["Rating"]) for r in rows) / len(rows), 2))
print("date range:", min(r["Date"] for r in rows), "->", max(r["Date"] for r in rows))
print("dev replies:", sum(1 for r in rows if r["Developer reply"]))
print("titles populated:", sum(1 for r in rows if r["Title"].strip()))

fee = re.compile(r"(?i)\b(brokerage|charge|charges|fee|fees|gst|stamp duty|deduction|deductions|deducted)\b")
fee_rows = [r for r in rows if fee.search(r["Review"])]
print("fee/charge rows (word-bounded):", len(fee_rows))
print("  ratings:", dict(sorted(Counter(int(r["Rating"]) for r in fee_rows).items())))
leaks = [r for r in rows if "fee" in r["Review"].lower() and not fee.search(r["Review"])]
print('substring "fee" leaks (feels/feedback):', len(leaks))

pos = re.compile(r"(?i)\b(good|nice|easy|great|smooth|useful|clean)\b")
low_pos = [r for r in rows if int(r["Rating"]) <= 2 and pos.search(r["Review"])]
print("low-rated rows with positive words (manual review):", len(low_pos))

# weekly fee trend
def wk(d):
    y, m, day = map(int, d.split("-"))
    import datetime
    dt = datetime.date(y, m, day)
    return f"{y}-W{(dt.timetuple().tm_yday - 1)//7 + 1:02d}"

by = Counter()
for r in rows:
    if fee.search(r["Review"]):
        by[wk(r["Date"])] += 1
print("weekly fee mentions:", dict(sorted(by.items())))
