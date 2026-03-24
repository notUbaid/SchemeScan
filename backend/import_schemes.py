import os, re, csv, sqlite3

ARCHIVE_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "archive", "updated_data.csv"
)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemes.db")

# ── Category / State helpers ───────────────────────────────────────────────────

def guess_category(text, csv_category=""):
    """Use CSV schemeCategory if present, otherwise guess from text."""
    if csv_category and csv_category.strip():
        cat = csv_category.strip().split(",")[0].strip()
        # Normalise to our known categories
        cat_lower = cat.lower()
        if any(w in cat_lower for w in ["agri", "rural", "environment", "fishery", "fishermen"]):
            return "Agriculture"
        if any(w in cat_lower for w in ["health", "medical"]):
            return "Health"
        if any(w in cat_lower for w in ["education", "learning", "skill"]):
            return "Education"
        if any(w in cat_lower for w in ["hous", "shelter", "awas"]):
            return "Housing"
        if any(w in cat_lower for w in ["employ", "business", "entrepreneur", "msme"]):
            return "Employment"
        if any(w in cat_lower for w in ["women", "child", "girl", "mahila"]):
            return "Women & Child"
        if any(w in cat_lower for w in ["social", "welfare", "pension", "empowerment"]):
            return "Social Security"
        return cat  # Return as-is if it doesn't match our buckets

    t = text.lower()
    if any(w in t for w in ["farm","kisan","krishi","agricultur","crop","irrigation","fishery","horticulture","seed","soil","animal husbandry","livestock"]): return "Agriculture"
    if any(w in t for w in ["health","medical","hospital","ayushman","disease","treatment","medicine","arogya","swasthya","surgery","cancer","tb"]): return "Health"
    if any(w in t for w in ["education","school","scholar","student","college","skill","training","literacy","fellowship","scholarship","coaching","tuition"]): return "Education"
    if any(w in t for w in ["housing","awas","home","shelter","construction","pucca","ghar","griha","house"]): return "Housing"
    if any(w in t for w in ["employ","job","livelihood","self-employ","startup","enterprise","rojgar","rozgar","yuva","unemployment","business","msme","loan"]): return "Employment"
    if any(w in t for w in ["women","mahila","girl","child","maternity","widow","beti","ladli","kanya","maternal","pregnant","nutrition"]): return "Women & Child"
    if any(w in t for w in ["pension","old age","senior","disabled","handicap","divyang","transgender","minority","destitute","leprosy","hiv"]): return "Social Security"
    return "Other"

def guess_state(text, level=""):
    """
    Detect actual state name from combined text.
    'level' column in CSV is 'State' or 'Central'.
    If level is Central, return 'Central' directly.
    Otherwise search for state name in text.
    """
    if level.strip().lower() == "central":
        return "Central"

    states = [
        "Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh","Goa",
        "Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka","Kerala",
        "Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram","Nagaland",
        "Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu","Telangana","Tripura",
        "Uttar Pradesh","Uttarakhand","West Bengal","Delhi","Chandigarh","Puducherry",
        "Lakshadweep","Jammu and Kashmir","Ladakh","Andaman"
    ]
    for s in states:
        if re.search(r'\b' + re.escape(s) + r'\b', text, re.I):
            return s
    return "Central"

def extract_age(text):
    m = re.search(r'(\d{1,2})\s*(?:to|-)\s*(\d{2,3})\s*years?', text, re.I)
    if m: return int(m.group(1)), int(m.group(2))
    m = re.search(r'above\s+(\d{1,2})\s*years?', text, re.I)
    if m: return int(m.group(1)), None
    m = re.search(r'minimum.*?(\d{2})\s*years?', text, re.I)
    if m: return int(m.group(1)), None
    return None, None

def extract_income(text):
    m = re.search(r'(?:below|less than|not exceed|upto?|within|not more than)\s*(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|crore|thousand)?', text, re.I)
    if not m:
        m = re.search(r'(?:annual income|income limit|family income)[^\d₹]*(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|crore|thousand)?', text, re.I)
    if m:
        num = float(m.group(1).replace(',', ''))
        unit = (m.group(2) or '').lower()
        if unit == 'lakh':     return int(num * 100000)
        if unit == 'crore':    return int(num * 10000000)
        if unit == 'thousand': return int(num * 1000)
        if num < 1000:         return int(num * 100000)
        return int(num)
    return None

def extract_gender(text):
    t = text.lower()
    keywords = ["only women","women only","for women","for girls","mahila only",
                "female only","widow","beti","kanya","girl child","woman"]
    if any(k in t for k in keywords): return "Female"
    return "All"

def extract_caste(text):
    t = text.lower()
    if "sc/st" in t or ("scheduled caste" in t and "scheduled tribe" in t): return "SC/ST"
    if "scheduled caste" in t: return "SC"
    if "scheduled tribe" in t: return "ST"
    if "obc" in t or "other backward" in t: return "OBC"
    if "minority" in t or "minorities" in t: return "Minority"
    return "All"

# ── DB setup ───────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS schemes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT NOT NULL,
        category      TEXT,
        state         TEXT,
        min_age       INTEGER,
        max_age       INTEGER,
        max_income    INTEGER,
        caste         TEXT DEFAULT 'All',
        gender        TEXT DEFAULT 'All',
        benefit_text  TEXT,
        eligibility   TEXT,
        documents     TEXT,
        apply_process TEXT,
        details       TEXT,
        source_file   TEXT,
        active        INTEGER DEFAULT 1
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS schemes_fts USING fts5(
        title,
        benefit_text,
        eligibility,
        details,
        content=schemes,
        content_rowid=id
    );

    CREATE TABLE IF NOT EXISTS events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type   TEXT,
        scheme_id    INTEGER,
        scheme_title TEXT,
        category     TEXT,
        user_state   TEXT,
        user_age     INTEGER,
        ts           TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS admin_schemes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT NOT NULL,
        category      TEXT,
        state         TEXT DEFAULT 'Central',
        min_age       INTEGER,
        max_age       INTEGER,
        max_income    INTEGER,
        caste         TEXT DEFAULT 'All',
        gender        TEXT DEFAULT 'All',
        benefit_text  TEXT,
        eligibility   TEXT,
        documents     TEXT,
        apply_process TEXT,
        details       TEXT,
        active        INTEGER DEFAULT 1,
        created_at    TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    print("✓ Database tables ready")

# ── Main import from CSV ───────────────────────────────────────────────────────

def run():
    csv_path = os.path.normpath(ARCHIVE_CSV)
    if not os.path.isfile(csv_path):
        print(f"ERROR: CSV not found: {csv_path}")
        return

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # Clear old data so we can re-run safely
    conn.execute("DELETE FROM schemes")
    conn.execute("DELETE FROM schemes_fts")
    conn.commit()

    inserted: int = 0
    skipped: int = 0
    c = conn.cursor()

    with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} scheme rows in CSV. Importing...\n")

    for row in rows:
        try:
            title         = (row.get("scheme_name") or "").strip()
            slug          = (row.get("slug") or "").strip()
            details       = (row.get("details") or "").strip()
            benefit_text  = (row.get("benefits") or "").strip()
            eligibility   = (row.get("eligibility") or "").strip()
            apply_process = (row.get("application") or "").strip()
            documents     = (row.get("documents") or "").strip()
            level         = (row.get("level") or "Central").strip()
            csv_category  = (row.get("schemeCategory") or "").strip()

            # Remove leading BOM/quote artifacts from title
            title = title.lstrip('\ufeff"').rstrip('"')

            if not title:
                skipped += 1  # pyre-ignore
                continue

            combined  = f"{title} {eligibility} {details} {benefit_text}"
            category  = guess_category(combined, csv_category)
            state     = guess_state(combined, level)
            min_age, max_age = extract_age(f"{eligibility} {details}")
            max_income = extract_income(f"{eligibility} {details}")
            gender    = extract_gender(f"{eligibility} {title}")
            caste     = extract_caste(eligibility)

            # Use slug as source_file reference
            source_file = slug if slug else re.sub(r'[^a-zA-Z0-9_]', '_', str(title)[:60])  # pyre-ignore

            c.execute("""
                INSERT INTO schemes
                (title, category, state, min_age, max_age, max_income, caste, gender,
                 benefit_text, eligibility, documents, apply_process, details, source_file)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (title, category, state, min_age, max_age, max_income, caste, gender,
                  benefit_text, eligibility, documents, apply_process, details, source_file))
            inserted += 1  # pyre-ignore

            if inserted % 300 == 0:
                conn.commit()
                print(f"  {inserted} schemes imported...")

        except Exception as e:
            print(f"  SKIP row '{row.get('scheme_name', '?')}': {e}")
            skipped += 1  # pyre-ignore

    conn.commit()

    print("\nBuilding full-text search index...")
    conn.execute("INSERT INTO schemes_fts(schemes_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    print(f"\n✅ Import complete!")
    print(f"   Inserted : {inserted}")
    print(f"   Skipped  : {skipped}")
    print(f"   DB saved : {DB_PATH}")

if __name__ == "__main__":
    run()
