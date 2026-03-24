"""
user_store.py — SchemeScan User Document & Profile Storage
-----------------------------------------------------------
• Saves OCR-extracted data from uploaded identity documents (per session)
• Builds a citizen profile from multiple documents
• Stores uploaded document images to disk (user_data/ folder)
• Provides retrieval helpers so the RAG engine can personalise answers
"""

import os, json, re, uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

USER_DATA_DIR = Path(__file__).parent / "user_data"
USER_DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = USER_DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
PROFILES_DIR = USER_DATA_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION / PROFILE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def new_session_id() -> str:
    uid: str = str(uuid.uuid4())
    return uid[:12]  # pyre-ignore


def _profile_path(session_id: str) -> Path:
    return PROFILES_DIR / f"{session_id}.json"


def load_profile(session_id: str) -> Dict:
    """Load a citizen profile by session id. Returns empty dict if not found."""
    p = _profile_path(session_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"session_id": session_id, "documents": [], "merged": {}}


def save_profile(session_id: str, profile: Dict):
    _profile_path(session_id).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ══════════════════════════════════════════════════════════════════════════════
# MERGE OCR DATA INTO PROFILE
# ══════════════════════════════════════════════════════════════════════════════

def merge_ocr_into_profile(session_id: str, ocr_data: Dict,
                            doc_type: str = "unknown",
                            image_path: Optional[str] = None) -> Dict:
    """
    Add a new OCR result to an existing session profile.
    Later documents override earlier ones for the same field (trust freshest doc).
    Returns the updated profile.
    """
    profile = load_profile(session_id)

    doc_record = {
        "doc_type":  doc_type,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "image_path": image_path,
        "ocr_data":  ocr_data,
    }
    profile.setdefault("documents", []).append(doc_record)

    # ── Merge fields into the "merged" profile ──────────────────────────────
    merged = profile.get("merged", {})

    FIELD_MAP = {
        "name":     "name",
        "age":      "age",
        "dob":      "dob",
        "gender":   "gender",
        "state":    "state",
        "district": "district",
        "aadhaar":  "aadhaar",
        "pan":      "pan",
    }

    for ocr_key, profile_key in FIELD_MAP.items():
        val = ocr_data.get(ocr_key)
        if val:
            merged[profile_key] = val

    # Derive extra useful facts
    if merged.get("dob") and not merged.get("age"):
        try:
            _m = re.search(r'\d{4}', str(merged["dob"]))
            if _m:
                yr = int(_m.group())
                merged["age"] = datetime.now().year - yr
        except Exception:
            pass

    profile["merged"] = merged
    save_profile(session_id, profile)
    return profile


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE STORAGE
# ══════════════════════════════════════════════════════════════════════════════

def save_uploaded_image(session_id: str, file_bytes: bytes,
                         original_filename: str) -> str:
    """
    Save an uploaded document image to disk.
    Returns the relative path (within user_data/uploads/).
    """
    ext  = Path(original_filename).suffix.lower() or ".jpg"
    safe = re.sub(r'[^a-z0-9_\-]', '_', session_id)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{safe}_{ts}{ext}"
    dest = UPLOADS_DIR / name
    dest.write_bytes(file_bytes)
    return str(dest)


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL PROFILE UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def update_profile_manual(session_id: str, fields: Dict) -> Dict:
    """
    Let the user manually update/correct their profile fields.
    Accepted fields: name, age, gender, state, district, caste, income, bpl
    """
    profile = load_profile(session_id)
    merged  = profile.get("merged", {})

    ALLOWED = {"name","age","gender","state","district",
               "caste","income","bpl","categories","language"}

    for k, v in fields.items():
        if k in ALLOWED and v not in (None, "", []):
            merged[k] = v

    profile["merged"] = merged
    save_profile(session_id, profile)
    return profile


# ══════════════════════════════════════════════════════════════════════════════
# SCHEME SEARCH PROFILE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def profile_to_search_params(session_id: str) -> Dict:
    """
    Convert a stored session profile into the parameters expected by /match.
    """
    profile = load_profile(session_id)
    m = profile.get("merged", {})

    return {
        "age":        m.get("age"),
        "state":      m.get("state"),
        "income":     m.get("income"),
        "caste":      m.get("caste", "All"),
        "gender":     m.get("gender", "All"),
        "bpl":        bool(m.get("bpl", False)),
        "categories": m.get("categories", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
# NATURAL LANGUAGE PROFILE EXTRACTION (for voice input)
# ══════════════════════════════════════════════════════════════════════════════

def parse_nl_profile(text: str) -> Dict:
    """
    Parse a natural language citizen description like:
    "I am a 45-year-old farmer in Gujarat with 2 acres of land,
     annual income below 1.5 lakh, SC category"
    Returns a profile dict.
    """
    profile: Dict = {}
    text_lower = text.lower()

    # Age
    m = re.search(r'(\d{1,3})[\s-]*(?:year|yr|sal|saal)s?\s*old', text_lower)
    if m: profile["age"] = int(m.group(1))

    # Income
    m = re.search(
        r'(?:income|earn|salary|kamai).*?(?:rs\.?|₹)?\s*([\d,.]+)\s*(lakh|lac|thousand)?',
        text_lower
    )
    if m:
        num  = float(m.group(1).replace(',','').replace('.',''))
        unit = (m.group(2) or '').lower()
        if 'lakh' in unit or 'lac' in unit: num *= 100_000
        elif 'thousand' in unit: num *= 1_000
        elif num < 500: num *= 100_000
        profile["income"] = int(num)

    # State
    STATES = [
        "Gujarat","Maharashtra","Rajasthan","Uttar Pradesh","Bihar",
        "West Bengal","Tamil Nadu","Karnataka","Kerala","Andhra Pradesh",
        "Madhya Pradesh","Punjab","Haryana","Assam","Odisha","Jharkhand",
        "Delhi","Telangana","Chhattisgarh","Uttarakhand","Himachal Pradesh",
        "Jammu and Kashmir",
    ]
    for s in STATES:
        if s.lower() in text_lower:
            profile["state"] = s
            break

    # Caste
    if re.search(r'\bsc/st\b', text_lower): profile["caste"] = "SC/ST"
    elif re.search(r'\bscheduled caste\b|\bsc\b', text_lower): profile["caste"] = "SC"
    elif re.search(r'\bscheduled tribe\b|\bst\b', text_lower): profile["caste"] = "ST"
    elif re.search(r'\bobc\b|other backward', text_lower): profile["caste"] = "OBC"

    # Gender
    if re.search(r'\bwoman\b|\bwife\b|\bwidow\b|\bgirl\b|\bfemale\b|\bmahila\b', text_lower):
        profile["gender"] = "Female"
    elif re.search(r'\bman\b|\bfarmer\b|\blabourer\b|\bmale\b', text_lower):
        profile["gender"] = "Male"

    # BPL
    if re.search(r'\bbpl\b|below poverty|ration card|garib', text_lower):
        profile["bpl"] = True

    # Categories by keywords
    cats = []
    if re.search(r'\bfarm|kisan|krishi|agricultur|crop\b', text_lower): cats.append("Agriculture")
    if re.search(r'\bhealth|sick|hospital|treatment\b', text_lower): cats.append("Health")
    if re.search(r'\bschool|education|college|student\b', text_lower): cats.append("Education")
    if re.search(r'\bhouse|ghar|home|shelter\b', text_lower): cats.append("Housing")
    if re.search(r'\bjob|employ|business|loan\b', text_lower): cats.append("Employment")
    if cats: profile["categories"] = cats

    return profile


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT TYPE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_doc_type(ocr_text: str) -> str:
    """Guess document type from OCR text."""
    t = ocr_text.upper()
    if "AADHAAR" in t or "UIDAI" in t or "UNIQUE IDENTIFICATION" in t:
        return "aadhaar"
    if "INCOME TAX" in t or re.search(r'[A-Z]{5}[0-9]{4}[A-Z]', t):
        return "pan"
    if "VOTER" in t or "ELECTION" in t:
        return "voter_id"
    if "RATION" in t:
        return "ration_card"
    if "INCOME CERTIFICATE" in t or "ANNUAL INCOME" in t:
        return "income_cert"
    if "CASTE CERTIFICATE" in t or "SCHEDULED" in t:
        return "caste_cert"
    return "unknown"
