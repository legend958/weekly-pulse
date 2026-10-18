import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pulse.cluster import classify  # noqa: E402

rows = list(csv.DictReader(open("groww-reviews.csv", encoding="utf-8")))
from collections import Counter

c = Counter()
unmatched = []
for r in rows:
    name = classify(r["Review"])
    c[name or "NONE"] += 1
    if name is None:
        unmatched.append(r)

print("classification:", dict(c.most_common()))
print("\nUNMATCHED (%d):" % len(unmatched))
for r in unmatched[:40]:
    print(f'  {r["Rating"]}* | {r["Review"][:100]}')
