import csv
import statistics

rows = list(csv.DictReader(open("groww-reviews.csv", encoding="utf-8")))

sig = {
    "fees": ["brokerage", "the other charges", "charges keep getting added",
             "charge breakup", "charge structure", "contract note"],
    "performance": ["laggy", "freezes every time", "app crashes", "take too long to load",
                    "becomes unresponsive", "keeps loading", "stale data", "loading screens"],
    "kyc": ["KYC", "verification has been pending", "account is still not activated",
            "verification step", "re-upload"],
    "withdrawal": ["withdrawal request", "withdrew has still not", "funds transfer status",
                   "payout is delayed", "settlement of my funds", "withdrawal will complete"],
    "uiux": ["layout looks cluttered", "too many taps", "hard to read on a small screen",
             "tax and profit-loss report", "navigation between sections",
             "buried deep inside menus", "watchlist and alerts"],
}

used = set()
for theme, pats in sig.items():
    idx = [i for i, r in enumerate(rows)
           if i not in used and any(p.lower() in r["Review"].lower() for p in pats)]
    used.update(idx)
    rr = [rows[i] for i in idx]
    avg = statistics.mean(int(x["Rating"]) for x in rr)
    print(f"{theme:12} n={len(rr):3}  avg={avg:.2f}")

praise = [r for i, r in enumerate(rows) if i not in used]
avg = statistics.mean(int(x["Rating"]) for x in praise)
print(f"{'praise/other':12} n={len(praise):3}  avg={avg:.2f}")
