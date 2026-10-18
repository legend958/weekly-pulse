"""
Generate a coherent mock Play Store review dataset for the Groww "Weekly Product Pulse" milestone.

Purpose
-------
The original groww-reviews.csv was unusable for the problem statement:
  - only 2 reviews mentioned a fee/charge (not "frequently misunderstood"),
  - ~39% of review texts were duplicated,
  - 97 of 210 low-rated reviews contained positive text (rating/text mismatch).

This script produces a reproducible dataset that supports the workflow:
  Step 1  clusters into <= 5 clean themes, with 1 recurring fee/charge confusion
  Step 2  a weekly product pulse with a real trend
  Step 3  a fee explainer derived from what users are actually confused about

Schema (unchanged): Date, Rating, Title, Review, App version, Helpful votes,
Developer reply, Reply date, Review URL

Deterministic: seeded RNG plus uuid5 review IDs, so re-running produces a
byte-identical groww-reviews.csv (only the seed + this code revision matter).

Run:  python scripts/generate_mock_reviews.py
"""

import csv
import json
import os
import random
import uuid
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEED = 20261016
random.seed(SEED)

START = date(2026, 7, 24)
END = date(2026, 10, 16)          # 85 days ~= 12 weeks
TOTAL_DAYS = (END - START).days + 1

PACKAGE = "com.nextbillion.groww"
REVIEW_URL = (
    "https://play.google.com/store/apps/details?id={pkg}&reviewId={rid}"
)

# --------------------------------------------------------------------------
# Theme content. Each theme builds reviews from opener + impact + ask so the
# text varies naturally (high uniqueness) while staying on one topic.
# --------------------------------------------------------------------------
THEMES = {
    "fees": {
        "n": 150,
        "titles": ["Hidden charges", "Too many charges", "Charges eating profits",
                   "Fee confusion", "Brokerage vs other charges", "Unexpected deductions"],
        "ratings": [2, 2, 3, 3, 3, 4],
        "openers": [
            "Brokerage is shown as Rs 20, but the other charges end up taking a large part of my profit",
            "I do not understand why my trade cost much more than the Rs 20 brokerage that is advertised",
            "The extra charges on every order are not explained clearly anywhere in the app",
            "I made a small gain but after all the deductions the final credit was far lower than expected",
            "There are too many hidden charges, and GST, stamp duty and DP charges keep getting added",
            "Every time I place an order the total charges surprise me because they are never shown upfront",
            "The charge breakup before confirming an order is confusing and I always end up paying more",
            "Charges are very high and they eat into the returns of small investors like me",
            "I was told brokerage is just Rs 20 but my contract note shows several other charges",
            "Please make the full charge structure visible so I know what I am actually paying",
        ],
        "impacts": [
            "This makes it hard to plan my trades",
            "It looks like the real cost is hidden until the order is placed",
            "For small trades the charges often cancel out the entire profit",
            "It is very frustrating for someone who is new to investing",
            "The gap between the shown amount and the credited amount is confusing",
            "I have started avoiding small orders because of this",
            "It makes me question whether trading here is worth it",
            "No one explains any of this during account setup",
        ],
        "asks": [
            "Please show a clear breakup of every charge",
            "Kindly explain these charges in simple language",
            "Please add a proper charges calculator",
            "The app should display all charges before the order is confirmed",
            "Please make the pricing details easier to find",
            "Hope the team looks into this soon",
        ],
    },
    "performance": {
        "n": 160,
        "titles": ["App is very slow", "Keeps freezing", "App crashes", "Bad performance",
                   "Lags badly", "Not stable"],
        "ratings": [1, 2, 2, 3, 3, 4],
        "openers": [
            "The app has become very laggy after the recent updates",
            "Groww freezes every time I try to open my portfolio",
            "The app crashes while I am placing an order during market hours",
            "Charts and prices take too long to load on the app",
            "The app hangs and becomes unresponsive on my phone",
            "Orders take a long time to place because the screen keeps loading",
            "The app closes by itself when I switch between sections",
            "Portfolio values do not refresh quickly and keep showing stale data",
            "Loading screens appear again and again while checking my holdings",
        ],
        "impacts": [
            "This is very risky when the market is moving fast",
            "I have missed entry opportunities because of the delay",
            "It happens consistently and on more than one device",
            "I have to restart the app again and again",
            "The overall experience is degrading day by day",
            "It makes simple tasks take much longer than they should",
        ],
        "asks": [
            "Please optimise the app performance",
            "Hope the team fixes these crashes soon",
            "Please test updates properly before releasing them",
            "A stable app is the least I expect",
            "Please fix this in the next update",
        ],
    },
    "kyc": {
        "n": 120,
        "titles": ["KYC pending", "Account not activated", "Verification stuck",
                   "KYC rejected", "Onboarding delay"],
        "ratings": [1, 2, 3, 3, 4],
        "openers": [
            "My KYC verification has been pending for many days without any update",
            "I completed all the documents but my account is still not activated",
            "My KYC was rejected without a clear reason",
            "Account opening took much longer than promised",
            "I keep getting the same pending status for my verification",
            "The documents I uploaded are not being processed at all",
            "There is no timeline for when my KYC will be approved",
            "I am stuck at the verification step and cannot start investing",
        ],
        "impacts": [
            "This has delayed my investing plan",
            "I had to re-upload the same documents multiple times",
            "Support only sends generic replies and nothing changes",
            "It is frustrating for a new user",
            "The process is not transparent at all",
        ],
        "asks": [
            "Please tell me clearly what is missing",
            "Kindly update the verification status",
            "Please speed up the KYC process",
            "Give a clear reason if something is rejected",
            "Please provide a proper status update",
        ],
    },
    "withdrawal": {
        "n": 115,
        "titles": ["Withdrawal stuck", "Money not received", "Payout delay",
                   "Settlement slow", "Funds pending"],
        "ratings": [1, 2, 3, 3, 4],
        "openers": [
            "My withdrawal request has been pending for days with no update",
            "The money I withdrew has still not reached my bank account",
            "The funds transfer status keeps showing pending even after the settlement date",
            "Withdrawal is taking much longer than the promised timeline",
            "I am unable to understand why my payout is delayed every time",
            "The amount I withdrew was debited but never credited to my bank",
            "Settlement of my funds is painfully slow compared to other apps",
            "There is no clear timeline shown for when my withdrawal will complete",
        ],
        "impacts": [
            "This makes it hard to manage my finances",
            "I had to raise multiple support tickets for the same issue",
            "It is very stressful when my own money is stuck",
            "The status never updates so I keep checking the app",
            "It has happened more than once now",
        ],
        "asks": [
            "Please show a clear expected date for the credit",
            "Kindly update the status in the app",
            "Please process withdrawals faster",
            "Tell us if there is a delay instead of keeping it pending",
        ],
    },
    "uiux": {
        "n": 115,
        "titles": ["Cluttered layout", "Too many taps", "Hard to navigate",
                   "Feature request", "Needs better design", "Tax report clarity"],
        "ratings": [3, 3, 4, 4, 5],
        "openers": [
            "The app layout looks cluttered after the recent update",
            "It takes too many taps to reach the portfolio details",
            "The charts and menus are hard to read on a small screen",
            "I would like a clearer tax and profit-loss report",
            "Navigation between sections is not intuitive",
            "The dashboard shows too much at once and gets confusing",
            "Important options are buried deep inside menus",
            "The watchlist and alerts could be much better organised",
        ],
        "impacts": [
            "This slows down simple tasks",
            "It is not beginner friendly",
            "I often cannot find the feature I need",
            "The experience is less simple than before",
        ],
        "asks": [
            "Please simplify the navigation",
            "Kindly add better filters and search",
            "Please improve the charts and readability",
            "Hope the team keeps improving the design",
            "Please consider this in the next update",
        ],
    },
    "praise": {
        "n": 40,
        "titles": ["Good app", "Easy to use", "Nice experience", "", "", ""],
        "ratings": [4, 5, 5, 5],
        "openers": [
            "Groww is simple and easy to use, especially for beginners",
            "The app is smooth and the SIP setup is very convenient",
            "I like how stocks and mutual funds are tracked in one place",
            "Placing an order is quick and the interface is clean",
            "Good experience overall, the app has improved a lot",
            "The dashboard puts all the important information in one place",
            "Very useful app for managing my investments",
            "The onboarding was easy and I started investing within minutes",
            "The watchlist makes it easy to follow companies",
            "Customer support helped me resolve my query quickly",
        ],
        "impacts": [
            "The experience has been reliable so far",
            "It works well on my phone",
            "I would recommend it to anyone starting out",
            "The design looks thoughtfully made",
        ],
        "asks": [
            "Please keep the experience this simple",
            "Thank you for the good work",
            "Hope the team keeps improving it",
            "Good experience overall",
        ],
    },
}

# A few deliberately distinctive lines, planted so the pulse can quote them verbatim.
QUOTES = {
    "fees": [
        "Brokerage is only Rs 20, but the other charges quietly take away a big part of my profit, and nothing in the app explains how much I will actually pay.",
        "I was shocked to see how much was deducted from my small trade after all the extra charges were added on top of the brokerage.",
        "Please show me a clear breakup of every charge before I place an order, because right now the final cost is always a surprise.",
    ],
    "performance": [
        "The app freezes right when I need to place an order during market hours, and by the time it recovers the price has already moved.",
    ],
    "kyc": [
        "My KYC has been stuck on pending for over a week and every support reply is the same generic message with no real update.",
    ],
}

DEV_REPLY_NEG = (
    "Hi, we're really sorry for the inconvenience. Please email us at "
    "support@groww.in and share the ticket ID that gets generated. This will "
    "help us address your issue more quickly. Thanks for your patience!"
)
DEV_REPLY_POS = (
    "Hi, we're delighted that you're enjoying Groww! Your support inspires us "
    "to keep working hard and delivering a great experience. Thank you so much "
    "for choosing us!"
)


def review_url(index: int) -> str:
    """Deterministic review id: uuid5 over a stable per-row index.

    ``uuid4`` would change every URL on each regeneration, so the CSV could not
    be reproduced from ``SEED`` alone. ``uuid5`` keeps the whole dataset
    byte-stable for a given seed + code revision.
    """
    rid = uuid.uuid5(uuid.NAMESPACE_URL, f"{PACKAGE}:{index}")
    return REVIEW_URL.format(pkg=PACKAGE, rid=rid)


def build_text(theme, combo_seed):
    data = THEMES[theme]
    rng = random.Random(combo_seed)
    opener = rng.choice(data["openers"])
    impact = rng.choice(data["impacts"])
    ask = rng.choice(data["asks"])
    parts = [opener[0].upper() + opener[1:] + "."]
    if rng.random() < 0.85:
        parts.append(impact + ".")
    if rng.random() < 0.8:
        parts.append(ask + ".")
    return " ".join(parts)


def round_robin_days(theme_weights):
    """Assign a date to each review, weighted so issues rise toward recent weeks."""
    rng = random.Random(SEED)
    days = []
    for theme, n in theme_weights:
        rising = theme in ("fees", "performance")
        weights = []
        for d in range(TOTAL_DAYS):
            frac = d / (TOTAL_DAYS - 1)
            w = 1 + (3.5 * frac if rising else 1.1 * frac)
            weights.append(w)
        for _ in range(n):
            days.append(rng.choices(range(TOTAL_DAYS), weights=weights, k=1)[0])
    return days


def main():
    theme_weights = [(t, d["n"]) for t, d in THEMES.items()]
    days = round_robin_days(theme_weights)
    rng = random.Random(SEED + 1)

    # Build a flat list of (theme, rating, text) first.
    rows_src = []
    for theme, data in THEMES.items():
        for i in range(data["n"]):
            rows_src.append((theme, rng.choice(data["ratings"]),
                             build_text(theme, combo_seed=SEED + len(rows_src))))
    rng.shuffle(rows_src)

    # Plant the distinctive quotable lines.
    for theme, quotes in QUOTES.items():
        idxs = [i for i, (t, _, _) in enumerate(rows_src) if t == theme]
        for q in quotes:
            i = rng.choice(idxs)
            t, rating, _ = rows_src[i]
            rows_src[i] = (t, min(rating, 3), q)

    # Pair each review with a date.
    paired = list(zip(rows_src, days))
    paired.sort(key=lambda x: x[1], reverse=True)  # newest first, like the export

    rows = []
    seen_texts = set()
    for index, ((theme, rating, text), day_offset) in enumerate(paired):
        # Guarantee uniqueness of review text.
        if text in seen_texts:
            suffix = rng.choice(
                [" Please look into it.", " This needs attention.", " It is quite frustrating.",
                 " The app should do better here.", " Kindly fix this soon."])
            text = text.rstrip(".") + "." + suffix
        seen_texts.add(text)

        d = START + timedelta(days=day_offset)
        version = f"18.{rng.randint(10, 25)}.{rng.randint(0, 4)}"
        helpful = rng.choices([0, 1, 2, 3, 4, 5, 7, 10, 14], weights=[45, 15, 12, 8, 6, 5, 4, 3, 2])[0]
        has_reply = rng.random() < 0.72
        reply = (DEV_REPLY_POS if rating >= 4 else DEV_REPLY_NEG) if has_reply else ""
        reply_date = ""
        if has_reply:
            reply_date = (d + (timedelta(days=1) if rng.random() < 0.3 else timedelta(0))).isoformat()
        title = rng.choice(THEMES[theme]["titles"])
        rows.append({
            "Date": d.isoformat(),
            "Rating": rating,
            "Title": title,
            "Review": text,
            "App version": version,
            "Helpful votes": helpful,
            "Developer reply": reply,
            "Reply date": reply_date,
            "Review URL": review_url(index),
            "_theme": theme,
        })

    out = os.path.join(ROOT, "groww-reviews.csv")
    cols = ["Date", "Rating", "Title", "Review", "App version", "Helpful votes",
            "Developer reply", "Reply date", "Review URL"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # Authoritative theme stats (ground truth from generation) -> docs/dataset_stats.json
    from collections import Counter

    def iso_week(d):
        return f"{d.year}-W{(d.timetuple().tm_yday - 1) // 7 + 1:02d}"

    theme_stats = {}
    for theme in THEMES:
        rs = [r for r in rows if r["_theme"] == theme]
        theme_stats[theme] = {
            "count": len(rs),
            "avg_rating": round(sum(r["Rating"] for r in rs) / len(rs), 2),
        }
    weekly_fee = Counter(iso_week(date.fromisoformat(r["Date"]))
                         for r in rows if r["_theme"] == "fees")
    stats = {
        "seed": SEED,
        "rows": len(rows),
        "date_range": [min(r["Date"] for r in rows), max(r["Date"] for r in rows)],
        "rating_mix": dict(sorted(Counter(r["Rating"] for r in rows).items())),
        "avg_rating": round(sum(r["Rating"] for r in rows) / len(rows), 2),
        "distinct_texts": len(seen_texts),
        "dev_replies": sum(1 for r in rows if r["Developer reply"]),
        "titles_populated": sum(1 for r in rows if r["Title"].strip()),
        "themes": theme_stats,
        "weekly_fee_mentions": dict(sorted(weekly_fee.items())),
    }
    docs_dir = os.path.join(ROOT, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    with open(os.path.join(docs_dir, "dataset_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    print(f"Wrote {len(rows)} rows to {out}")
    print("Rating mix:", stats["rating_mix"])
    print("Themes:", {t: v["count"] for t, v in theme_stats.items()})
    print("Wrote docs/dataset_stats.json")


if __name__ == "__main__":
    main()
