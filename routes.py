"""Route definitions for CNX cheap-flights scanner."""

# (origin, dest, name, threshold_usd)
ROUTES = [
    # Note: hub routes (BKK→KUL etc.) are in HUB_ROUTES below
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
    ("CNX", "SIN", "Singapore", 120),
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


# Hub-to-hub routes for backpacker chain planner
HUB_ROUTES = [
    # Bangkok hubs
    ("BKK", "KUL", "Bangkok→KL", 60),
    ("BKK", "SIN", "Bangkok→Singapore", 80),
    ("BKK", "HAN", "Bangkok→Hanoi", 60),
    ("BKK", "RGN", "Bangkok→Yangon", 60),
    ("BKK", "HKG", "Bangkok→Hong Kong", 100),
    ("BKK", "TPE", "Bangkok→Taipei", 120),
    ("DMK", "KUL", "Bangkok-DMK→KL", 60),
    ("DMK", "SIN", "Bangkok-DMK→Singapore", 80),
    ("DMK", "HAN", "Bangkok-DMK→Hanoi", 50),
    ("DMK", "RGN", "Bangkok-DMK→Yangon", 50),
    # KL hub
    ("KUL", "BKK", "KL→Bangkok", 60),
    ("KUL", "SIN", "KL→Singapore", 30),
    ("KUL", "HAN", "KL→Hanoi", 60),
    ("KUL", "HKG", "KL→Hong Kong", 80),
    ("KUL", "TPE", "KL→Taipei", 100),
    # Singapore hub
    ("SIN", "BKK", "Singapore→Bangkok", 80),
    ("SIN", "KUL", "Singapore→KL", 30),
    ("SIN", "HAN", "Singapore→Hanoi", 60),
    ("SIN", "HKG", "Singapore→Hong Kong", 100),
]


def seed_routes(db):
    """Insert all routes into DB, skip existing."""
    db.executemany(
        "INSERT OR IGNORE INTO routes (origin, dest, name, threshold, nonstop) VALUES (?,?,?,?,1)",
        [(o, d, n, t) for o, d, n, t in ROUTES],
    )
    db.executemany(
        "INSERT OR IGNORE INTO routes (origin, dest, name, threshold, nonstop) VALUES (?,?,?,?,0)",
        [(o, d, n, t) for o, d, n, t in HUB_ROUTES],
    )
    db.commit()
