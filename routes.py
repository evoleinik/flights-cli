"""Route definitions for CNX cheap-flights scanner."""

# (origin, dest, name, threshold_usd)
ROUTES = [
    # International (21)
    ("CNX", "AUH", "Abu Dhabi", 250),
    ("CNX", "CAN", "Guangzhou", 80),
    ("CNX", "CKG", "Chongqing", 80),
    ("CNX", "HAN", "Hanoi", 55),
    ("CNX", "HKG", "Hong Kong", 90),
    ("CNX", "ICN", "Seoul", 150),
    ("CNX", "JHG", "Jinghong", 60),
    ("CNX", "KHH", "Kaohsiung", 100),
    ("CNX", "KIX", "Osaka", 150),
    ("CNX", "KMG", "Kunming", 60),
    ("CNX", "KUL", "Kuala Lumpur", 50),
    ("CNX", "LPQ", "Luang Prabang", 80),
    ("CNX", "MDL", "Mandalay", 60),
    ("CNX", "PEK", "Beijing", 120),
    ("CNX", "PUS", "Busan", 150),
    ("CNX", "PVG", "Shanghai", 120),
    ("CNX", "RGN", "Yangon", 80),
    ("CNX", "SIN", "Singapore", 80),
    ("CNX", "TFU", "Chengdu", 80),
    ("CNX", "TPE", "Taipei", 100),
    ("CNX", "XIY", "Xi'an", 100),
    # Domestic (11)
    ("CNX", "BKK", "Bangkok-Suv", 25),
    ("CNX", "DMK", "Bangkok-DMK", 20),
    ("CNX", "HDY", "Hat Yai", 25),
    ("CNX", "HHQ", "Hua Hin", 30),
    ("CNX", "HKT", "Phuket", 30),
    ("CNX", "KBV", "Krabi", 30),
    ("CNX", "KKC", "Khon Kaen", 25),
    ("CNX", "URT", "Surat Thani", 30),
    ("CNX", "USM", "Ko Samui", 40),
    ("CNX", "UTH", "Udon Thani", 25),
    ("CNX", "UTP", "Pattaya", 30),
]


def seed_routes(db):
    """Insert all routes into DB, skip existing."""
    db.executemany(
        "INSERT OR IGNORE INTO routes (origin, dest, name, threshold, nonstop) VALUES (?,?,?,?,1)",
        [(o, d, n, t) for o, d, n, t in ROUTES],
    )
    db.commit()
