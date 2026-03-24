"""
SchemeScan Backend — main.py  v7.0
AI backend: Ollama (phi3:mini) — fully offline, CSV-powered

v7.0 IMPROVEMENTS:
  • Massively expanded profile extraction — Hindi, Hinglish, aliases, housewife, zero-income
  • AI confirms what it understood before asking follow-ups (warm, conversational)
  • Smarter question priority — context-aware, not just fixed order
  • Merged scheme sections — one clean block per scheme for the AI (saves context window)
  • Prompt completely rewritten — no mode labels, no raw scores, clean natural conversation
  • Fixed age scoring bug — no bonus if age range didn't match
  • Open-to-all schemes handled properly (small bonus instead of neutral)
  • Retrieval query enriched from full profile for better BM25 results
  • Ollama params tuned for phi3:mini natural language quality
"""

import os, re, io, sqlite3, math, uuid, json, csv
from pathlib import Path
from typing import Any, Optional, List, Dict, Tuple
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks, Request # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from fastapi.responses import JSONResponse # type: ignore
from pydantic import BaseModel # type: ignore
import httpx # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent
DB_PATH      = BASE_DIR / "schemes.db"
ARCHIVE_CSV  = BASE_DIR.parent / "archive" / "updated_data.csv"
SCHEMES_DIR  = ARCHIVE_CSV   # kept for legacy references

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_TAGS = f"{OLLAMA_URL}/api/tags"
MODEL_NAME  = os.environ.get("OLLAMA_MODEL", "phi3:mini")

# ─────────────────────────────────────────────────────────────────────────────
# APP + CORS
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="SchemeScan API", version="7.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"], expose_headers=["*"],
)

@app.exception_handler(Exception)
async def _all_errors(request: Request, exc: Exception):
    return JSONResponse(
        status_code=getattr(exc, "status_code", 500),
        content={"detail": str(exc)},
        headers={"Access-Control-Allow-Origin": "*"},
    )

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def rows(result): return [dict(r) for r in result]

def log_event(event_type, scheme_id=None, scheme_title=None,
              category=None, user_state=None, user_age=None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO events (event_type,scheme_id,scheme_title,category,user_state,user_age) VALUES (?,?,?,?,?,?)",
            (event_type, scheme_id, scheme_title, category, user_state, user_age)
        )
        conn.commit(); conn.close()
    except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# INLINE BM25
# ─────────────────────────────────────────────────────────────────────────────

def _tok(text: str) -> list:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()

class _BM25:
    def __init__(self, corpus: list, k1=1.5, b=0.75):
        self.k1 = k1; self.b = b; self.corpus = corpus
        N = len(corpus)
        self.avdl = sum(len(d) for d in corpus) / max(N, 1)
        df: dict = {}
        for doc in corpus:
            for t in set(doc):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log((N-f+0.5)/(f+0.5)+1) for t,f in df.items()}

    def scores(self, query: List[str]) -> List[float]:
        sc: List[float] = [0.0]*len(self.corpus)
        for t in query:
            idf = self.idf.get(t, 0.0)
            for i, doc in enumerate(self.corpus):
                tf = doc.count(t); dl = len(doc)
                d = tf + self.k1*(1-self.b+self.b*dl/max(self.avdl,1))
                sc[i] += idf*tf*(self.k1+1)/max(d,1e-9)
        return sc

# ─────────────────────────────────────────────────────────────────────────────
# RAG DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class _Chunk:
    def __init__(self, fname: str, title: str, section: str, text: str, state: str) -> None:
        self.fname:    str  = fname
        self.title:    str  = title
        self.section:  str  = section
        self.text:     str  = text
        self.state:    str  = state
        self._all_sections: Dict[str, str] = {}

_rag_chunks: List[_Chunk] = []
_rag_bm25: Optional[_BM25] = None
_rag_ready = False

def _state_from_text(text: str) -> str:
    for s in ["Gujarat","Maharashtra","Rajasthan","Uttar Pradesh","Bihar",
              "West Bengal","Tamil Nadu","Karnataka","Kerala","Andhra Pradesh",
              "Madhya Pradesh","Punjab","Haryana","Assam","Odisha","Jharkhand",
              "Delhi","Telangana","Chhattisgarh","Uttarakhand","Himachal Pradesh",
              "Goa","Manipur","Meghalaya","Mizoram","Nagaland","Arunachal Pradesh",
              "Sikkim","Tripura","Jammu and Kashmir","Ladakh","Lakshadweep",
              "Chandigarh","Puducherry","Andaman"]:
        if re.search(r'\b'+re.escape(s)+r'\b', text, re.I): return s
    return "Central"

def build_rag():
    global _rag_chunks, _rag_bm25, _rag_ready
    if not ARCHIVE_CSV.exists():
        print(f"WARNING: archive CSV not found at {ARCHIVE_CSV}"); return

    chunks = []
    SECTION_COLS = [
        ("BENEFITS",            "benefits"),
        ("ELIGIBILITY",         "eligibility"),
        ("DOCUMENTS",           "documents"),
        ("APPLICATION PROCESS", "application"),
        ("DETAILS",             "details"),
    ]
    with open(str(ARCHIVE_CSV), "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = str((row.get("scheme_name") or "")).strip().lstrip('\ufeff"').rstrip('"')
                slug  = str((row.get("slug") or title)).strip()[:60] # type: ignore
                level = str((row.get("level") or "Central")).strip()
                combined = " ".join([
                    row.get("details", ""),
                    row.get("eligibility", ""),
                    row.get("benefits", ""),
                ])
                st = _state_from_text(combined) if level.lower() != "central" else "Central"
                for sec_label, col in SECTION_COLS:
                    txt = (row.get(col) or "").strip()
                    if txt and len(txt) > 20:
                        chunks.append(_Chunk(slug, title, sec_label, txt, st))
            except Exception:
                pass

    if not chunks:
        print("WARNING: RAG found 0 chunks"); return
    corpus = [_tok(f"{c.title} {c.section} {c.text}") for c in chunks]
    _rag_chunks = chunks; _rag_bm25 = _BM25(corpus)
    _rag_ready = True
    print(f"RAG ready: {len(chunks)} chunks from archive CSV.")

def rag_retrieve(query: str, top_k: int = 20, state_filter: Optional[str] = None) -> List[_Chunk]:
    if not _rag_ready or _rag_bm25 is None: return []
    sc: List[float] = list(getattr(_rag_bm25, "scores", lambda x: [])(_tok(query))) # type: ignore
    if state_filter:
        for i, c in enumerate(_rag_chunks):
            if c.state.lower() == state_filter.lower(): sc[i] *= 1.6  # strong state boost
            elif c.state == "Central": sc[i] *= 1.1                   # slight central boost
    ranked = sorted(range(len(sc)), key=lambda i: sc[i], reverse=True)
    return [_rag_chunks[i] for i in ranked[0:top_k] if sc[i] > 0] # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE EXTRACTION  (comprehensive — English + Hindi + Hinglish)
# ─────────────────────────────────────────────────────────────────────────────

INDIAN_STATES = [
    "andhra pradesh","arunachal pradesh","assam","bihar","chhattisgarh","goa",
    "gujarat","haryana","himachal pradesh","jharkhand","karnataka","kerala",
    "madhya pradesh","maharashtra","manipur","meghalaya","mizoram","nagaland",
    "odisha","punjab","rajasthan","sikkim","tamil nadu","telangana","tripura",
    "uttar pradesh","uttarakhand","west bengal","delhi","chandigarh","puducherry",
    "andaman","lakshadweep","jammu","ladakh","j&k","new delhi",
]

STATE_ALIASES = {
    "up":  "Uttar Pradesh",   "mp":  "Madhya Pradesh",   "hp":  "Himachal Pradesh",
    "wb":  "West Bengal",     "ap":  "Andhra Pradesh",   "tn":  "Tamil Nadu",
    "jk":  "Jammu and Kashmir", "j&k": "Jammu and Kashmir",
    "uk":  "Uttarakhand",     "cg":  "Chhattisgarh",     "gj":  "Gujarat",
    "mh":  "Maharashtra",     "rj":  "Rajasthan",        "hr":  "Haryana",
    "pb":  "Punjab",          "ka":  "Karnataka",        "kl":  "Kerala",
    "br":  "Bihar",           "jh":  "Jharkhand",        "or":  "Odisha",
    "ts":  "Telangana",       "dl":  "Delhi",
}

def _canonical_state(raw: str) -> Optional[str]:
    raw = raw.strip().lower()
    if raw in STATE_ALIASES: return STATE_ALIASES[raw]
    for s in INDIAN_STATES:
        if raw == s or raw in s or s in raw:
            return s.title()
    return None

def extract_profile_from_history(history: list) -> Dict:
    """
    Parse the entire conversation to build a user profile.
    Handles English, Hindi, Hinglish, abbreviations, and natural speech.
    Extracts: age, gender, state, caste, income, occupation,
              education, bpl, disability, marital_status
    """
    profile: Dict = {}
    full_text = " ".join(
        m.get("content","") for m in history if m.get("role") == "user"
    ).lower()

    # ── Age ───────────────────────────────────────────────────────────────────
    for pattern in [
        r'\b(?:i\s+am|i\'m|i am|meri umar|meri age|mera age|age\s+is|age\s*[:=]?)\s*(\d{1,2})\b',
        r'\b(\d{1,2})\s*(?:years?\s*old|yr\.?\s*old|yrs?\.?\s*old)\b',
        r'\bage[\s:=]+(\d{1,2})\b',
        r'\b(\d{1,2})\s*(?:saal|sal|varsh|year)\b',
        r'\bmujhe\s+(\d{1,2})\s*(?:saal|sal)?\s*(?:ho gaye|ho gaya|hoge|ki umra)\b',
        r'\b(\d{1,2})\s*ka\s*(?:hoon|hu|hun)\b',
    ]:
        m = re.search(pattern, full_text)
        if m:
            age = int(m.group(1))
            if 5 < age < 100: profile["age"] = age; break

    # ── Gender ────────────────────────────────────────────────────────────────
    female_pattern = (
        r'\b(female|woman|women|girl|mahila|aurat|ladki|beti|she|her|housewife|'
        r'grihani|widow|vidhwa|divorcee|mother|maa|behan|sister|daughter|bahu|'
        r'i am a woman|i am female|i\'m a woman|i\'m female)\b'
    )
    male_pattern = (
        r'\b(male|man|men|boy|aadmi|mard|ladka|beta|he|him|husband|pita|baap|'
        r'bhai|brother|son|i am a man|i am male|i\'m a man|i\'m male)\b'
    )
    if re.search(female_pattern, full_text): profile["gender"] = "Female"
    elif re.search(male_pattern, full_text): profile["gender"] = "Male"

    # ── State ─────────────────────────────────────────────────────────────────
    # Direct state name match
    for state in INDIAN_STATES:
        if re.search(r'\b' + re.escape(state) + r'\b', full_text):
            profile["state"] = state.title(); break
    # Short code aliases
    if "state" not in profile:
        for alias, canonical in STATE_ALIASES.items():
            if re.search(r'(?:^|\s)' + re.escape(alias) + r'(?:\s|$)', full_text):
                profile["state"] = canonical; break
    # "from X" / "live in X" / "rehta hoon X" etc.
    if "state" not in profile:
        for pattern in [
            r'(?:from|live in|living in|staying in|resident of|i am from|reside in|'
            r'rehta hoon|rehti hoon|rehta hu|rehna|se hoon|se hun)\s+([a-z\s]{3,30})',
        ]:
            m = re.search(pattern, full_text)
            if m:
                guessed = _canonical_state(m.group(1).strip())
                if guessed: profile["state"] = guessed; break

    # ── Caste ─────────────────────────────────────────────────────────────────
    if re.search(r'\bsc\s*/\s*st\b|\bscst\b|\bsc\/st\b', full_text):
        profile["caste"] = "SC/ST"
    elif re.search(r'\bscheduled\s+caste\b|\b(?<![a-z])sc(?![a-z])\b|\bdalit\b|\bharijaan\b', full_text):
        profile["caste"] = "SC"
    elif re.search(r'\bscheduled\s+tribe\b|\b(?<![a-z])st(?![a-z])\b|\btribal\b|\badivasi\b', full_text):
        profile["caste"] = "ST"
    elif re.search(r'\bobc\b|\bother\s+backward\b|\bpichda\b|\bpichdi\s+jati\b', full_text):
        profile["caste"] = "OBC"
    elif re.search(r'\bminority\b|\bmuslim\b|\bchristian\b|\bsikh\b|\bbuddh\b|\bjain\b|\bparsi\b', full_text):
        profile["caste"] = "Minority"
    elif re.search(r'\bgeneral\b|\bunreserved\b|\bopen\s+category\b|\bforward\s+caste\b|\bfc\b|\bbrahmin\b|\brajput\b|\bbaniya\b', full_text):
        profile["caste"] = "General"

    # ── Income ────────────────────────────────────────────────────────────────
    # Zero / no income detection first
    if re.search(r'\bno income\b|\bzero income\b|\bkoi income nahi\b|\bkoi kamai nahi\b|\bnot earning\b|\bnot employed\b', full_text):
        profile["income"] = 0
        profile["bpl"] = True
    else:
        # Pattern: "income X lakh" or "earn X thousand" etc.
        for pat in [
            r'(?:income|earn(?:ing|ings)?|salary|wages?|kamai|kamaata|kamata|aay|amdani)\D{0,15}([\d,]+\.?\d*)\s*(lakh|lac|l\b|crore|k\b|thousand|hazar)',
            r'(?:income|earn(?:ing|ings)?|salary|wages?)\s*(?:is|of|=|:)?\s*(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|lac|l\b|crore|k\b|thousand)?',
            r'(?:rs\.?|₹)\s*([\d,]+\.?\d*)\s*(lakh|lac|crore|k|thousand)?\s*(?:per year|annually|annual|yearly|mahina|month)',
        ]:
            m = re.search(pat, full_text)
            if m:
                try:
                    num = float(m.group(1).replace(",",""))
                    unit = (m.group(2) or "").lower().strip()
                    if unit in ("lakh","lac","l"):     num *= 100000
                    elif unit in ("crore",):           num *= 10000000
                    elif unit in ("k",):               num *= 1000
                    elif unit in ("thousand","hazar"): num *= 1000
                    elif num < 1000:                   num *= 100000  # bare "2" = 2 lakh
                    profile["income"] = int(num)
                    break
                except Exception: pass

    # ── BPL ──────────────────────────────────────────────────────────────────
    if re.search(r'\bbpl\b|\bbelow poverty\b|\bantodaya\b|\bantyodaya\b|\bration card\b|\bgareeb\b|\bpoor\b|\bvery poor\b|\bkaafi garib\b', full_text):
        profile["bpl"] = True

    # ── Occupation ────────────────────────────────────────────────────────────
    occ_patterns = [
        ("student",        r'\bstudent\b|\bstudying\b|\bin college\b|\bin school\b|\buniversity\b|\bpadhai\b|\bpadh raha\b|\bpadh rahi\b|\bcollege mein\b|\bschool mein\b'),
        ("farmer",         r'\bfarmer\b|\bkisan\b|\bagricultur\b|\bkhet\b|\bfarming\b|\bfarm\b|\bkhetibadi\b|\bcrops?\b|\bkisaan\b'),
        ("fisherman",      r'\bfish(?:erm(?:an|en)|ing|eries)?\b|\bmachhu(?:aar|ara)?\b|\bmatsya\b'),
        ("weaver",         r'\bweav\b|\bhandloom\b|\bbunkar\b|\bkarigari\b|\bharcraft\b'),
        ("construction",   r'\bconstruction\b|\bnirman\b|\bbuilding worker\b|\braj mistri\b|\bcement\b'),
        ("daily_worker",   r'\bdaily wage\b|\blabour\b|\bmazdoor\b|\bkaamgar\b|\bdihadi\b|\bkaam mazdoor\b|\bunorganised\b|\bunorganized\b|\blabourer\b'),
        ("housewife",      r'\bhousewife\b|\bgriha(?:ni|sthi)\b|\bgharkam\b|\bghar ka kaam\b|\bnot working\b|\bstay at home\b|\bnaukri nahi\b|\bno job\b|\bghar mein rehti\b'),
        ("unemployed",     r'\bunemployed\b|\bberojgar\b|\blooking for (?:job|work)\b|\bjob dhundh\b|\bjob nahi\b|\bno job\b|\bkoi kaam nahi\b'),
        ("self_employed",  r'\bshop\b|\bbusiness\b|\bself.?employ\b|\bdukan\b|\bvyapar\b|\bkirana\b|\bsmall business\b|\bmsme\b|\bentrepreneur\b|\bkhud ka\b'),
        ("govt_employee",  r'\bgovernment (?:job|employee|servant|worker)\b|\bgovt (?:job|employee)\b|\bsarkari (?:naukri|kaam)\b|\bpublic servant\b'),
    ]
    for occ, pat in occ_patterns:
        if re.search(pat, full_text):
            profile["occupation"] = occ; break

    # ── Education ─────────────────────────────────────────────────────────────
    if re.search(r'\bphd\b|\bdoctorate\b|\bpost.?grad(?:uat)?\b|\b(?<![a-z])pg(?![a-z])\b|\bm\.?tech\b|\bmba\b|\bmasters?\b|\bm\.?a\b|\bm\.?sc\b', full_text):
        profile["education"] = "postgraduate"
    elif re.search(r'\bgraduate\b|\bdegree\b|\bb\.?a\b|\bb\.?com\b|\bb\.?sc\b|\bb\.?tech\b|\bba pass\b|\bbachelor\b|\bgrad(?:uat)?\b', full_text):
        profile["education"] = "graduate"
    elif re.search(r'\b12th\b|\bhsc\b|\binter(?:mediate)?\b|\bplus two\b|\b12 pass\b|\bclass 12\b|\bstandard 12\b', full_text):
        profile["education"] = "12th"
    elif re.search(r'\b10th\b|\bssc\b|\bmatric(?:ulation)?\b|\b10 pass\b|\bclass 10\b|\bstandard 10\b', full_text):
        profile["education"] = "10th"
    elif re.search(r'\biti\b|\bdiploma\b|\bpolytechnic\b|\bvocational\b', full_text):
        profile["education"] = "iti_diploma"
    elif re.search(r'\bno school\b|\bno education\b|\billiterate\b|\banpadh\b|\bclass [1-8]\b|\bprimary\b|\bnirakshar\b|\bcannot read\b', full_text):
        profile["education"] = "primary_or_none"

    # ── Disability ────────────────────────────────────────────────────────────
    if re.search(r'\bdisab(?:led|ility)\b|\bdivyang\b|\bhandicap\b|\bblind\b|\bdeaf\b|\bwheelchair\b|\bviklaang\b|\bpwd\b|\bdumb\b|\bspeech impair\b|\bdifferen(?:t|tly) abled\b', full_text):
        profile["disability"] = True

    # ── Marital Status ────────────────────────────────────────────────────────
    if re.search(r'\bwidow(?:er)?\b|\bvidhwa\b|\bparini(?:ta)?\b|\bpati gaye\b|\bpati nahi raha\b|\bhusband (?:died|passed|expired|nahi raha)\b', full_text):
        profile["marital_status"] = "widowed"
    elif re.search(r'\bmarried\b|\bwife\b|\bhusband\b|\bshadi\b|\bvivah(?:it)?\b|\bspouse\b|\bpati\b|\bpatni\b', full_text):
        profile["marital_status"] = "married"
    elif re.search(r'\bsingle\b|\bunmarried\b|\bnot married\b|\bno wife\b|\bno husband\b|\bkuwaara\b|\bkuwari\b|\bavivahit\b', full_text):
        profile["marital_status"] = "single"

    return profile


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE QUESTIONS — context-aware, prioritized
# ─────────────────────────────────────────────────────────────────────────────

# Full questions list — asked in order if no smart context available
_BASE_QUESTIONS = [
    ("state",
     "Which **state** do you live in?\n"
     "_(Example: Gujarat, Maharashtra, Bihar, Rajasthan, Uttar Pradesh, Delhi…)_"),
    ("gender",
     "Are you **male** or **female**?"),
    ("age",
     "How **old** are you? Please tell me your age in years.\n"
     "_(Example: 25, 35, 45…)_"),
    ("caste",
     "What is your **caste category**?\n"
     "• **General** (no reservation)\n"
     "• **OBC** (Other Backward Class)\n"
     "• **SC** (Scheduled Caste / Dalit)\n"
     "• **ST** (Scheduled Tribe / Adivasi)\n"
     "• **Minority** (Muslim, Christian, Sikh, Buddhist, Jain)\n\n"
     "Just type the one that applies to you."),
    ("occupation",
     "What do you **currently do** for work?\n"
     "• Student\n"
     "• Farmer / Kisan\n"
     "• Daily wage / Labour\n"
     "• Housewife\n"
     "• Unemployed (looking for work)\n"
     "• Small shop / Business\n"
     "• Government job\n"
     "• Construction worker\n"
     "• Fisherman\n"
     "• Other (please describe)"),
    ("income",
     "What is your **approximate total family income per year**?\n"
     "_(Examples: ₹30,000 · ₹1.5 lakh · ₹3 lakh · No income)_"),
    ("education",
     "What is your **highest education level**?\n"
     "• No school / Illiterate\n"
     "• Class 1–8 (primary)\n"
     "• 10th pass\n"
     "• 12th pass\n"
     "• ITI / Diploma\n"
     "• Graduate (BA/B.Sc/B.Com/BTech)\n"
     "• Post-Graduate (MA/MTech/MBA/PhD)"),
    ("bpl",
     "Do you have a **BPL ration card** (Below Poverty Line) or **Antyodaya card**?\n"
     "_(Yes / No)_"),
    ("disability",
     "Do you have any **disability** (Divyang / Viklaang)?\n"
     "_(Yes / No)_"),
    ("marital_status",
     "What is your **marital status**?\n"
     "• Single / Unmarried\n"
     "• Married\n"
     "• Widowed"),
]
_BASE_QUESTION_KEYS = [q[0] for q in _BASE_QUESTIONS]


def get_next_question(profile: Dict) -> Optional[Tuple[str, str]]:
    """
    Return the next most important question to ask.
    Priority order is fixed, but skip fields already known.
    """
    for field, question in _BASE_QUESTIONS:
        if field not in profile:
            return (field, question)
    return None  # All fields known


def profile_completeness(profile: Dict) -> int:
    """Return number of filled profile fields (0–9)."""
    return sum(1 for f in _BASE_QUESTION_KEYS if f in profile)


def summarise_profile(profile: Dict) -> str:
    """
    Return a short, warm, human-readable summary of what we know.
    Used by the AI to confirm back to the user before asking follow-ups.
    """
    parts = []
    if "age"    in profile: parts.append(f"{profile['age']} years old")
    if "gender" in profile: parts.append(profile["gender"].lower())
    if "state"  in profile: parts.append(f"from {profile['state']}")
    if "caste"  in profile: parts.append(f"{profile['caste']} category")
    if "occupation" in profile:
        occ_display = {
            "student": "student", "farmer": "farmer", "fisherman": "fisherman",
            "weaver": "weaver", "construction": "construction worker",
            "daily_worker": "daily wage worker", "housewife": "housewife",
            "unemployed": "unemployed", "self_employed": "small business owner",
            "govt_employee": "government employee",
        }
        parts.append(str(occ_display.get(profile["occupation"], profile["occupation"].replace("_"," "))))
    if "income" in profile:
        inc = profile["income"]
        if inc == 0:    parts.append("no income")
        elif inc < 100000: parts.append(f"₹{inc:,}/year income")
        else:           parts.append(f"₹{inc/100000:.1f} lakh/year income")
    if profile.get("bpl"):        parts.append("BPL card holder")
    if profile.get("disability"): parts.append("person with disability")
    if "marital_status" in profile and profile["marital_status"] == "widowed":
        parts.append("widow/widower")
    if not parts: return ""
    return ", ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# SMART SCHEME SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_chunks_for_profile(chunks: List[_Chunk], profile: Dict) -> List[Tuple[_Chunk, float]]:
    """
    Score each retrieved chunk against the user profile.
    Groups chunks by scheme title, merges all sections, returns best match per scheme.
    """
    # First: merge all chunks for the same scheme title into ONE entry
    merged: Dict[str, Dict[str, Any]] = {}   # key -> {title, fname, state, text, sections, best_score}
    for chunk in chunks:
        key = str(chunk.title).lower().strip()[:80] # type: ignore
        if key not in merged:
            merged[key] = {
                "title": chunk.title, "fname": chunk.fname, "state": chunk.state,
                "text": chunk.text, "sections": {chunk.section: chunk.text}
            }
        else:
            merged[key]["sections"][chunk.section] = chunk.text
            merged[key]["text"] += "\n" + chunk.text  # accumulate text for scoring

    scheme_scores: Dict[str, Tuple] = {}

    for key, entry in merged.items():
        score: float = 0.0
        text_lower: str = (entry.get("text") or "").lower() + " " + (entry.get("title") or "").lower()

        # ── State ──────────────────────────────────────────────────────────────
        if profile.get("state"):
            user_state = profile["state"].lower()
            if entry["state"].lower() == user_state:
                score += 50.0   # Exact state match = top priority
            elif entry["state"] == "Central":
                score += 12.0   # Central scheme = available to all
            else:
                score -= 30.0   # Wrong state = heavy penalty

        # ── Age ────────────────────────────────────────────────────────────────
        if profile.get("age"):
            age = profile["age"]
            age_matches = re.findall(r'(\d{1,2})\s*(?:to|–|-)\s*(\d{2,3})\s*years?', text_lower, re.I)
            has_age_restriction = bool(age_matches)
            age_matched = False

            for lo, hi in age_matches:
                if int(lo) <= age <= int(hi):
                    score = float(getattr(score, "__add__", lambda x: 0)(25.0)); age_matched = True; break # type: ignore

            # Also check "below X years" / "upto X years"
            m = re.search(r'(?:below|under|upto?|not\s+exceed|maximum)\s*(\d{1,3})\s*years?', text_lower)
            if m and age <= int(m.group(1)):
                score = float(getattr(score, "__add__", lambda x: 0)(15.0)); age_matched = True # type: ignore
                has_age_restriction = True

            # Minimum age check
            m2 = re.search(r'(?:above|minimum|at\s+least)\s*(\d{1,2})\s*years?', text_lower)
            if m2 and age >= int(m2.group(1)):
                score = float(getattr(score, "__add__", lambda x: 0)(10.0)); age_matched = True # type: ignore

            # If scheme specifies age but user doesn't match → penalise
            if has_age_restriction and not age_matched:
                score -= 20.0
            # No age restriction = open to all, small bonus
            if not has_age_restriction:
                score += 5.0

        # ── Gender ──────────────────────────────────────────────────────────────
        if profile.get("gender"):
            gender = profile["gender"].lower()
            is_female_scheme = bool(re.search(
                r'\b(women\s*only|only\s*women|female\s*only|for\s*women|mahila|beti|kanya|girl child|widow|vidhwa)\b',
                text_lower))
            is_male_scheme = bool(re.search(r'\b(men\s*only|only\s*men|male\s*only|for\s*men)\b', text_lower))
            if is_female_scheme:
                score += 20.0 if gender == "female" else -30.0
            elif is_male_scheme:
                score += 20.0 if gender == "male" else -30.0
            else:
                score += 4.0  # Gender-neutral = slightly favoured

        # ── Caste ───────────────────────────────────────────────────────────────
        if profile.get("caste"):
            caste = profile["caste"].lower()
            sc_in   = "scheduled caste" in text_lower or re.search(r'\bsc\b', text_lower) or "dalit" in text_lower
            st_in   = "scheduled tribe" in text_lower or re.search(r'\bst\b', text_lower) or "adivasi" in text_lower
            obc_in  = "other backward" in text_lower or re.search(r'\bobc\b', text_lower)
            min_in  = "minority" in text_lower or "minorities" in text_lower
            has_caste_restriction = sc_in or st_in or obc_in or min_in

            if has_caste_restriction:
                user_matches = (
                    (caste in ("sc","sc/st") and sc_in) or
                    (caste in ("st","sc/st") and st_in) or
                    (caste == "obc" and obc_in) or
                    (caste == "minority" and min_in)
                )
                score += 30.0 if user_matches else -25.0
            else:
                score += 6.0  # Open to all castes = good

        # ── Income / BPL ────────────────────────────────────────────────────────
        if "income" in profile:
            inc = profile["income"]
            m = re.search(
                r'(?:below|upto?|not\s+exceed|maximum\s+income|less\s+than|income\s+limit)\s*'
                r'(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|crore)?',
                text_lower
            )
            if m:
                try:
                    limit = float(m.group(1).replace(",",""))
                    unit  = (m.group(2) or "").lower()
                    if unit == "lakh":  limit *= 100000
                    elif unit == "crore": limit *= 10000000
                    elif limit < 500:   limit *= 100000
                    score += 20.0 if inc <= limit else -15.0
                except Exception: pass
            else:
                score += 5.0  # No income restriction = open
        if profile.get("bpl") and re.search(r'\bbpl\b|\bbelow poverty\b|\bantyodaya\b|\bration\b', text_lower):
            score += 18.0

        # ── Occupation ──────────────────────────────────────────────────────────
        if profile.get("occupation"):
            occ = profile["occupation"]
            occ_kw = {
                "student":       ["student","studying","education","scholarship","fellowship","school","college"],
                "farmer":        ["farmer","kisan","agriculture","farming","krishi","fasal","crop","horticulture","irrigation"],
                "daily_worker":  ["labour","worker","mazdoor","daily wage","construction","unorganized","labourer","wage"],
                "housewife":     ["women","mahila","self help group","shg","maternity","griha"],
                "unemployed":    ["unemployed","berojgar","employment","skill","training","job","youth","rojgar"],
                "self_employed": ["entrepreneur","business","self-employ","startup","msme","shop","enterprise","loan"],
                "govt_employee": ["government","sarkari","employee","service","pension"],
                "construction":  ["construction","nirman","builder","labour","worker","building"],
                "fisherman":     ["fisherm","fishing","fish","machhu","matsya","aqua"],
                "weaver":        ["weav","handloom","bunkar","handicraft","textile","craft"],
            }
            if occ in occ_kw:
                hits = sum(1 for kw in occ_kw[occ] if kw in text_lower)
                score += min(hits * 8, 30)

        # ── Education ───────────────────────────────────────────────────────────
        if profile.get("education"):
            edu = profile["education"]
            edu_kw = {
                "primary_or_none": ["primary","literacy","anpadh","illiterate","basic"],
                "10th":            ["10th","matric","secondary","class 10","ssc"],
                "12th":            ["12th","higher secondary","hsc","intermediate","plus two","class 12"],
                "iti_diploma":     ["iti","diploma","polytechnic","vocational","technical"],
                "graduate":        ["graduate","degree","bachelor","graduation","college"],
                "postgraduate":    ["post-graduate","postgraduate","master","phd","research","fellow"],
            }
            if edu in edu_kw:
                hits = sum(1 for kw in edu_kw[edu] if kw in text_lower)
                score += min(hits * 6, 18)

        # ── Disability ──────────────────────────────────────────────────────────
        if profile.get("disability") and re.search(r'\bdisab\b|\bdivyang\b|\bhandicap\b|\bpwd\b|\bviklaang\b', text_lower):
            score += 25.0

        # ── Widow ───────────────────────────────────────────────────────────────
        if profile.get("marital_status") == "widowed" and re.search(r'\bwidow\b|\bvidhwa\b', text_lower):
            score += 25.0

        # Create a representative chunk object with merged text
        best_section = "ELIGIBILITY" if "ELIGIBILITY" in entry["sections"] else list(entry["sections"].keys())[0] if entry["sections"] else "DETAILS"
        rep_chunk = _Chunk(entry["fname"], entry["title"], best_section, entry["text"][:1200], entry["state"])
        rep_chunk._all_sections = entry["sections"]  # store all for formatting

        key2 = entry["title"].lower().strip()[:80]
        if key2 not in scheme_scores or score > scheme_scores[key2][1]:
            scheme_scores[key2] = (rep_chunk, score)

    return sorted(scheme_scores.values(), key=lambda x: x[1], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEME FORMATTING FOR PROMPT  (clean, compact, AI-friendly)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_schemes_for_prompt(scored_chunks: List[Tuple], max_schemes: int = 4) -> str:
    """
    Format top-N schemes into a clean, compact block for the AI prompt.
    Merges sections, strips boilerplate, truncates intelligently.
    """
    if not scored_chunks:
        return "NO SCHEME DATA AVAILABLE."

    parts = []
    for i, (chunk, _score) in enumerate(scored_chunks[0:max_schemes]): # type: ignore
        sections = getattr(chunk, "_all_sections", {getattr(chunk, "section", ""): getattr(chunk, "text", "")}) # type: ignore

        benefit   = str(sections.get("BENEFITS", ""))[:500].strip() # type: ignore
        eligib    = str(sections.get("ELIGIBILITY", ""))[:600].strip() # type: ignore
        docs      = str(sections.get("DOCUMENTS", ""))[:300].strip() # type: ignore
        apply_p   = str(sections.get("APPLICATION PROCESS", ""))[:400].strip() # type: ignore

        # Build clean block
        block = f"SCHEME {i+1}: {getattr(chunk, 'title', '')}\nState/Level: {getattr(chunk, 'state', '')}\n"
        if benefit:  block += f"BENEFIT: {benefit}\n"
        if eligib:   block += f"ELIGIBILITY: {eligib}\n"
        if docs:     block += f"DOCUMENTS: {docs}\n"
        if apply_p:  block += f"HOW TO APPLY: {apply_p}\n"
        block += f"[ref: {getattr(chunk, 'fname', '')}]"

        parts.append(block)

    return "\n\n" + ("─"*60 + "\n").join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# LANGUAGE INSTRUCTIONS
# ─────────────────────────────────────────────────────────────────────────────

LANG_INSTRUCTIONS = {
    "en": "Respond in clear, simple, warm English. Use very short sentences. Avoid all jargon — say 'money you get' not 'disbursement amount'. Imagine talking to a village farmer with no education.",
    "hi": "पूरा जवाब सरल हिंदी में दें। बिल्कुल आसान शब्द इस्तेमाल करें। मान लो तुम एक अनपढ़ किसान से बात कर रहे हो। अगर यूज़र हिंदी या हिंग्लिश में बात करे, तो हिंदी में जवाब दो।",
    "gu": "સંપૂર્ણ જવાબ સરળ ગુજરાતીમાં આપો। ખૂબ સરળ શબ્દો વાપરો।",
    "mr": "संपूर्ण उत्तर साध्या मराठीत द्या. खूप सोपे शब्द वापरा.",
    "ta": "எளிய தமிழில் முழு பதிலை கொடுங்கள். மிகவும் எளிய வார்த்தைகளை பயன்படுத்துங்கள்.",
    "te": "సరళమైన తెలుగులో సమాధానం ఇవ్వండి. చాలా సులభమైన పదాలను వాడండి.",
    "bn": "সহজ বাংলায় সম্পূর্ণ উত্তর দিন। খুব সহজ শব্দ ব্যবহার করুন।",
    "kn": "ಸರಳ ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸಿ. ತುಂಬಾ ಸರಳ ಪದಗಳನ್ನು ಬಳಸಿ.",
    "pa": "ਸਾਦੀ ਪੰਜਾਬੀ ਵਿੱਚ ਜਵਾਬ ਦਿਓ। ਬਹੁਤ ਸੌਖੇ ਸ਼ਬਦ ਵਰਤੋ।",
}


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT  (rewritten for natural, warm, intelligent conversation)
# ─────────────────────────────────────────────────────────────────────────────

def build_system_prompt(
    profile: Dict,
    completeness: int,
    profile_summary: str,
    next_question: Optional[Tuple[str, str]],
    schemes_text: str,
    lang_code: str,
) -> str:
    lang_inst  = LANG_INSTRUCTIONS.get(lang_code, LANG_INSTRUCTIONS["en"])
    next_q     = next_question[1] if next_question else None
    next_field = next_question[0] if next_question else None

    # Format known profile for AI reference
    profile_lines = []
    if "age"            in profile: profile_lines.append(f"  • Age: {profile['age']}")
    if "gender"         in profile: profile_lines.append(f"  • Gender: {profile['gender']}")
    if "state"          in profile: profile_lines.append(f"  • State: {profile['state']}")
    if "caste"          in profile: profile_lines.append(f"  • Caste: {profile['caste']}")
    if "income"         in profile: profile_lines.append(f"  • Annual Income: ₹{profile['income']:,}" if profile["income"] > 0 else "  • Annual Income: None/Zero")
    if "occupation"     in profile: profile_lines.append(f"  • Occupation: {profile['occupation'].replace('_',' ').title()}")
    if "education"      in profile: profile_lines.append(f"  • Education: {profile['education']}")
    if "bpl"            in profile: profile_lines.append(f"  • BPL Card: Yes")
    if "disability"     in profile: profile_lines.append(f"  • Disability: Yes")
    if "marital_status" in profile: profile_lines.append(f"  • Marital Status: {profile['marital_status'].title()}")
    known_profile = "\n".join(profile_lines) if profile_lines else "  (Nothing known yet)"

    missing_fields = [f for f in _BASE_QUESTION_KEYS if f not in profile]

    # ── Determine what the AI should do ──────────────────────────────────────
    no_schemes_msg = (
        "Sorry, I could not find any schemes that match your profile in my database right now. "
        "This can happen if your state has limited coverage or the criteria is very specific. "
        "Please try describing yourself in more detail — especially your state, age, and occupation. "
        "You can also visit myscheme.gov.in to search directly."
    )

    if completeness == 0:
        task_instruction = """
TASK: The user is starting a new conversation. You know NOTHING about them yet.
Do ONLY these THREE things, in order:

1. Introduce yourself warmly in ONE sentence: "Hi! I'm PolicyPilot 👋 — I can find the best free government schemes for you based on your details."
2. In ONE more short sentence, explain what you'll do: "Just answer a few quick questions and I'll show you all the schemes you qualify for!"
3. Ask ONLY this one question, nothing more:

   "First — which state or union territory do you live in? (For example: Rajasthan, Gujarat, Bihar, Delhi…)"

STRICT RULES: Do NOT list any schemes. Max 4 lines. Be warm and encouraging.
"""

    elif completeness < 4:
        # Warm, conversational phrasing for each question field
        conv_questions = {
            "state":          "Which state or union territory do you live in?",
            "age":            "How old are you?",
            "gender":         "Are you male or female? (I ask because many schemes are gender-specific)",
            "occupation":     "What kind of work do you do? For example \u2014 farmer, student, daily worker, housewife, business owner, or something else?",
            "caste":          "Do you belong to SC, ST, OBC, or General category? (Many schemes are category-specific, so this really helps!)",
            "income":         "What is your family's approximate yearly income? For example \u2014 \u20b930,000, \u20b91 lakh, \u20b93 lakh, or no income?",
            "education":      "What is your highest education? For example \u2014 no schooling, 8th pass, 10th pass, 12th pass, or graduate?",
            "bpl":            "Do you have a BPL ration card or Antyodaya card? (Yes or No)",
            "disability":     "Do you have any disability? (Yes or No)",
            "marital_status": "Are you currently single, married, or widowed?",
        }
        conv_q = conv_questions.get(next_field, next_q) if next_field else "Can you tell me more about yourself?"
        confirmed_line = (f"Got it! So you are {profile_summary}. " if profile_summary else "Got it! ")

        task_instruction = f"""TASK: Still collecting basic info ({completeness} things known so far).
Do ONLY these two things:
1. In ONE line, confirm what you heard: \"{confirmed_line}\"
2. Then ask ONLY this ONE question, nothing more:

   \"{conv_q}\"

STRICT RULES:
- Do NOT show any schemes yet. You need more information first.
- Ask ONLY one question. Never two questions at once.
- Max 4 lines total. Be warm and encouraging.
"""

    elif completeness < 6:
        conv_questions = {
            "caste":          "One more thing \u2014 do you belong to SC, ST, OBC, or General category?",
            "income":         "What is your approximate yearly family income? (e.g. \u20b950,000, \u20b91 lakh, \u20b93 lakh)",
            "education":      "What is your highest education level? (e.g. 10th pass, 12th pass, graduate)",
            "bpl":            "Do you have a BPL ration card? (Yes or No) \u2014 this unlocks extra schemes!",
            "disability":     "Do you have any disability? (Yes or No)",
            "marital_status": "Are you single, married, or widowed?",
            "occupation":     "What kind of work do you do?",
            "gender":         "Are you male or female?",
        }
        conv_q = conv_questions.get(next_field, next_q) if next_field else "Any other detail you can share?"

        task_instruction = f"""TASK: You have {completeness} details ({profile_summary}). Getting closer \u2014 a few more questions!
Do exactly this:
1. Warmly confirm in ONE line: \"Great! So you are {profile_summary}.\"
2. Say you're almost ready to find their schemes (ONE short sentence).
3. Ask ONLY this ONE question:

   \"{conv_q}\"

STRICT RULES:
- Do NOT show full scheme recommendations yet.
- One question only. Max 5 lines. Stay warm.
"""

    elif completeness < 8:
        # Stage 6-7: tease scheme names, ask last question
        conv_questions = {
            "bpl":            "Do you have a BPL ration card? (Yes or No) \u2014 this could unlock several extra schemes!",
            "disability":     "Do you have any disability? (Yes or No) \u2014 there are dedicated schemes I can find for you!",
            "marital_status": "Are you single, married, or a widow/widower?",
            "education":      "What is your highest education level?",
            "income":         "What is your approximate yearly family income?",
        }
        conv_q = conv_questions.get(next_field, next_q) if next_field else None
        schemes_available = schemes_text and schemes_text.strip() not in ("", "NO SCHEME DATA AVAILABLE.")
        tease = "Mention 1-2 SCHEME NAMES that might apply (names only, no details). Example: \"I can already see PM Kisan and Bihar Startup Policy might apply for you!\"" if schemes_available else ""
        q_line = f'4. Ask ONLY this ONE final question: \"{conv_q}\"' if conv_q else "4. You have enough info \u2014 give full recommendations now."

        task_instruction = f"""TASK: {completeness}/9 fields known ({profile_summary}). Almost done!
Do in order:
1. Warmly confirm: \"Almost there! You are {profile_summary}.\"
2. Say you can already see some matching schemes.
3. {tease}
{q_line}

STRICT RULES:
- Do NOT give full scheme details yet.
- Ask at most ONE question.
- Max 6 lines.
"""

    else:
        schemes_available = schemes_text and schemes_text.strip() not in ("", "NO SCHEME DATA AVAILABLE.")
        fallback_block = f"\n\n{no_schemes_msg}" if not schemes_available else ""

        task_instruction = f"""
TASK: You have enough information. Give clear scheme recommendations NOW.

The user's profile:
{known_profile}
{fallback_block}

RECOMMENDATION FORMAT \u2014 use EXACTLY this structure for each scheme:

---
\u2705 **[Scheme Name]**
\ud83c\udfd9\ufe0f Level: [State name] / Central Government
\ud83d\udcb0 Benefit: [What money or help they get \u2014 1 short sentence. Use \u20b9 amounts.]
\u2714\ufe0f You qualify because: [Why this person qualifies \u2014 use their age/caste/state/job]
\ud83d\udccb Documents: Aadhaar, [2-3 specific docs]
\ud83d\udcdd How to apply: [2-3 short steps]
---

RULES:
- Recommend TOP 2-3 best-matching schemes ONLY.
- Prefer state-specific schemes when state is known.
- ONLY use information from SCHEME DATA. Never invent.
- Simplest possible words \u2014 imagine explaining to a 10-year-old.
- End with: "\ud83d\udcac Want more details about any of these? Just ask!"
- If no schemes match: say sorry kindly and suggest myscheme.gov.in.
"""

    return f"""You are PolicyPilot — a warm, friendly assistant that helps ordinary Indian citizens find free government schemes and benefits.

LANGUAGE RULE: {lang_inst}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USER PROFILE (what you know so far):
{known_profile}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{task_instruction}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NON-NEGOTIABLE RULES:
1. NEVER recommend more than 3 schemes per response.
2. NEVER invent information. Use ONLY the SCHEME DATA below.
3. NEVER ask more than ONE follow-up question per message.
4. ALWAYS be warm and encouraging — like a helpful older sibling.
5. Use ONLY simple words. Say "money you get" not "disbursement amount". Say "how to apply" not "application procedure".
6. If the user writes in Hindi, Hinglish, or any Indian language — reply in the SAME language naturally.
7. Prefer state-specific schemes over central ones when the user's state is known.
8. If no schemes match, say so honestly and kindly. NEVER make up a scheme.
9. CRITICAL — STOP generating after you ask your question. Do NOT write what the user might say next. Do NOT simulate a user reply. Do NOT write "Thank you, I am..." or continue the dialogue as the user. Your response ends with the question mark.
10. NEVER put words in the user's mouth. NEVER invent what the user said. If the user did not provide a fact, treat it as unknown.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEME DATA (use ONLY this — never invent):
{schemes_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Now respond following the TASK above. Be KIND, SIMPLE, and SHORT. END YOUR RESPONSE AFTER YOUR QUESTION — do not continue the conversation by simulating what the user might say.
"""


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, build_rag) # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
# 1. HEALTH
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0]
    conn.close()
    return {
        "status": "running", "app": "SchemeScan", "version": "7.0",
        "schemes_in_db": n, "rag_ready": _rag_ready,
        "rag_chunks": len(_rag_chunks), "ai_backend": f"Ollama ({MODEL_NAME})",
    }

# ─────────────────────────────────────────────────────────────────────────────
# 2. MATCH
# ─────────────────────────────────────────────────────────────────────────────

class Profile(BaseModel):
    age:            Optional[int]  = None
    state:          Optional[str]  = None
    income:         Optional[int]  = None
    caste:          Optional[str]  = "All"
    gender:         Optional[str]  = "All"
    bpl:            Optional[bool] = False
    occupation:     Optional[str]  = None
    marital_status: Optional[str]  = None
    categories:     Optional[list] = []

@app.post("/match")
def match_schemes(p: Profile, limit: int = 80):
    conn = get_db(); conds: List[str] = ["s.active = 1"]; par: List[Any] = []
    if p.age:
        conds += ["(s.min_age IS NULL OR s.min_age <= ?)", "(s.max_age IS NULL OR s.max_age >= ?)"]
        par += [p.age, p.age]
    if p.income is not None:
        conds.append("(s.max_income IS NULL OR s.max_income >= ?)"); par.append(p.income)
    if p.state:
        conds.append("(s.state = 'Central' OR s.state = ?)"); par.append(p.state)
    if p.caste and p.caste not in ("All","General","Prefer not to say"):
        conds.append("(s.caste = 'All' OR s.caste LIKE ?)"); par.append(f"%{p.caste}%")
    if p.gender and p.gender not in ("All","Prefer not to say"):
        conds.append("(s.gender = 'All' OR s.gender = ?)"); par.append(p.gender)
    if p.occupation:
        conds.append("(s.eligibility LIKE ? OR s.details LIKE ?)"); par.extend([f"%{p.occupation}%", f"%{p.occupation}%"])
    if p.marital_status:
        conds.append("(s.eligibility LIKE ? OR s.details LIKE ?)"); par.extend([f"%{p.marital_status}%", f"%{p.marital_status}%"])
    if p.categories:
        ph = ",".join("?" for _ in p.categories) # type: ignore
        conds.append(f"s.category IN ({ph})"); par.extend(p.categories) # type: ignore
    where = " AND ".join(conds)
    sql = f"""
        SELECT id,title,category,state,min_age,max_age,max_income,caste,gender,
               benefit_text,eligibility,documents,apply_process,details,'imported' AS source_flag
        FROM schemes s WHERE {where}
        UNION ALL
        SELECT id,title,category,state,min_age,max_age,max_income,caste,gender,
               benefit_text,eligibility,documents,apply_process,details,'admin' AS source_flag
        FROM admin_schemes s WHERE {where}
        ORDER BY category LIMIT ?
    """
    result = conn.execute(sql, par+par+[limit]).fetchall()
    conn.close()
    log_event("search", user_state=p.state, user_age=p.age)
    return {"count": len(result), "schemes": rows(result)}

# ─────────────────────────────────────────────────────────────────────────────
# 3. SEARCH
# ─────────────────────────────────────────────────────────────────────────────

_STOPWORDS = {
    'i','am','a','an','the','is','in','on','at','to','for','of','and','or',
    'with','my','me','we','are','be','been','have','has','do','did','was',
    'were','this','that','it','from','by','as','age','old','about','year',
    'years','card','want','need','looking','help','please','can','will',
    'not','no','yes','so','but','if','than','then','also','more','some',
    'any','all','very','just','out','into','after','before','up','down',
}

@app.get("/search")
def search_schemes(q: str, state: Optional[str] = None, limit: int = 40):
    if len(q.strip()) < 2: raise HTTPException(400, "Query too short")
    kw = [t for t in re.sub(r"[^a-zA-Z0-9\s]"," ",q).lower().split() if len(t)>2 and t not in _STOPWORDS]
    if not kw: return {"count":0,"schemes":[]}
    fts_q = " OR ".join(kw[0:5]) # type: ignore
    conn = get_db()
    try:
        if state:
            result = conn.execute("""
                SELECT s.id,s.title,s.category,s.state,s.benefit_text,
                       s.eligibility,s.documents,s.apply_process,s.details
                FROM schemes s JOIN schemes_fts f ON s.id=f.rowid
                WHERE schemes_fts MATCH ? AND (s.state = 'Central' OR s.state = ?)
                ORDER BY rank LIMIT ?
            """, (fts_q, state, limit)).fetchall()
        else:
            result = conn.execute("""
                SELECT s.id,s.title,s.category,s.state,s.benefit_text,
                       s.eligibility,s.documents,s.apply_process,s.details
                FROM schemes s JOIN schemes_fts f ON s.id=f.rowid
                WHERE schemes_fts MATCH ? ORDER BY rank LIMIT ?
            """, (fts_q, limit)).fetchall()
    except Exception: result = []
    conn.close(); log_event("search")
    return {"count": len(result), "schemes": rows(result)}

# ─────────────────────────────────────────────────────────────────────────────
# 4. SCHEME DETAIL
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/scheme/{scheme_id}")
def get_scheme(scheme_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM schemes WHERE id=?", (scheme_id,)).fetchone()
    if not row: row = conn.execute("SELECT * FROM admin_schemes WHERE id=?", (scheme_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Scheme not found")
    s = dict(row)
    log_event("view", scheme_id=s["id"], scheme_title=s["title"], category=s["category"])
    return s

# ─────────────────────────────────────────────────────────────────────────────
# 5. SMART RAG QUERY — the main AI endpoint
# ─────────────────────────────────────────────────────────────────────────────

class RAGQuery(BaseModel):
    question:     str
    language:     Optional[str] = "en"
    state_filter: Optional[str] = None
    session_id:   Optional[str] = None   # Sent by frontend — stored for future use
    history:      Optional[list] = []

@app.post("/rag/query")
async def rag_query(req: RAGQuery):
    lang_code = str((req.language or "en")).lower()[0:2] # type: ignore

    # ── Step 1: Extract full user profile from conversation ───────────────────
    full_history = list(req.history or [])
    full_history.append({"role": "user", "content": req.question})
    profile = extract_profile_from_history(full_history)

    if req.state_filter and "state" not in profile:
        profile["state"] = req.state_filter

    completeness    = profile_completeness(profile)
    profile_summary = summarise_profile(profile)
    next_question   = get_next_question(profile)

    # ── Step 2: Build an enriched retrieval query from profile ────────────────
    retrieval_parts = [req.question]
    if profile.get("state"):       retrieval_parts.append(profile["state"])
    if profile.get("occupation"):  retrieval_parts.append(profile["occupation"].replace("_"," "))
    if profile.get("caste"):       retrieval_parts.append(profile["caste"])
    if profile.get("education"):   retrieval_parts.append(profile["education"].replace("_"," "))
    if profile.get("gender") == "Female":             retrieval_parts.append("women scheme mahila beti")
    if profile.get("disability"):                     retrieval_parts.append("divyang disabled persons pwd")
    if profile.get("marital_status") == "widowed":    retrieval_parts.append("widow vidhwa relief")
    if profile.get("bpl"):                            retrieval_parts.append("bpl below poverty ration")
    if profile.get("occupation") == "farmer":         retrieval_parts.append("kisan agriculture crop farming")
    if profile.get("occupation") == "student":        retrieval_parts.append("scholarship education fellowship")
    if profile.get("occupation") in ("unemployed","housewife"): retrieval_parts.append("employment skill training")

    retrieval_query = " ".join(retrieval_parts)

    # ── Step 3: Retrieve & score ──────────────────────────────────────────────
    raw_chunks = rag_retrieve(retrieval_query, top_k=25, state_filter=profile.get("state"))
    scored = score_chunks_for_profile(raw_chunks, profile) if raw_chunks else []

    # DB fallback
    sources = []
    if not scored:
        kw = [t for t in re.sub(r'[^a-zA-Z0-9\s]',' ', req.question).lower().split()
              if len(t)>2 and t not in _STOPWORDS][0:4] # type: ignore
        conn = get_db(); hits = []
        if kw:
            try:
                hits = conn.execute("""
                    SELECT s.title, s.benefit_text, s.eligibility, s.apply_process
                    FROM schemes s JOIN schemes_fts f ON s.id=f.rowid
                    WHERE schemes_fts MATCH ? LIMIT 5
                """, (" OR ".join(kw),)).fetchall()
            except Exception: pass
        conn.close()
        schemes_text = "\n\n".join(
            f"SCHEME: {r['title']}\nBENEFITS: {(r['benefit_text'] or '')[:400]}\nELIGIBILITY: {(r['eligibility'] or '')[:400]}"
            for r in hits
        ) if hits else "No scheme data found for this query."
        sources = [{"title": r["title"], "section": "DB"} for r in hits]
    else:
        # Only send full scheme data to the AI when we're in recommendation stage (≥8 fields)
        max_schemes = 3 if completeness >= 8 else 2
        schemes_text = _fmt_schemes_for_prompt(scored, max_schemes=max_schemes)
        sources = [{"file": getattr(c, "fname", ""), "title": getattr(c, "title", ""), "state": getattr(c, "state", "")}
                   for c, _ in scored[0:5]] # type: ignore

    # ── Step 4: Build system prompt ───────────────────────────────────────────
    system_prompt = build_system_prompt(
        profile=profile,
        completeness=completeness,
        profile_summary=profile_summary,
        next_question=next_question,
        schemes_text=schemes_text,
        lang_code=lang_code,
    )

    # ── Step 5: Build conversation messages (last 16 turns) ───────────────────
    messages = []
    for h in (req.history or [])[-16:]: # type: ignore
        role    = h.get("role","user")
        content = h.get("content","")
        if role in ("user","assistant") and content:
            messages.append({"role": role, "content": str(content)[0:1000]})  # cap per message # type: ignore
    messages.append({"role": "user", "content": req.question})

    # ── Step 6: Call Ollama ───────────────────────────────────────────────────
    try:
        resp = httpx.post(
            OLLAMA_CHAT,
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ],
                "stream": False,
                "options": {
                    "temperature":    0.35,   # Slightly warmer = more natural language
                    "top_p":          0.9,
                    "top_k":          40,
                    "repeat_penalty": 1.15,
                    "num_predict":    1400,   # Slightly more room for recommendations
                    "num_ctx":        4096,
                },
            },
            timeout=240.0,
        )
        resp.raise_for_status()
        answer = resp.json().get("message", {}).get("content", "").strip()
        if not answer:
            answer = "⚠️ No response from AI. Please try again."

        # ── Post-processing: strip hallucinated "user reply" continuations ──────
        # Small models like phi3:mini sometimes answer their own question.
        # Detect patterns like "---\nThank you, I am..." or "User: ..." after the last "?"
        if completeness < 8 and answer:
            import re as _re
            # Find the last occurrence of a question mark — that's where the AI should stop
            q_pos = answer.rfind("?")
            if q_pos != -1:
                # Check if there's substantial text after the last "?" that looks like a user reply
                after_q = answer[q_pos + 1:].strip()
                # Patterns that indicate the AI is roleplaying as user
                fake_reply_patterns = [
                    r'^[-─━=]{2,}',          # separator line after ?
                    r'(?i)^thank\s+you',      # "Thank you, I am..."
                    r'(?i)^i\s+am\b',         # "I am a member..."
                    r'(?i)^my\s+',            # "My category is..."
                    r'(?i)^user\s*:',         # "User: ..."
                    r'(?i)^citizen\s*:',      # "Citizen: ..."
                    r'(?i)^applicant\s*:',    # "Applicant: ..."
                    r'(?i)^yes\b',            # "Yes, I have..."
                    r'(?i)^no\b',             # "No, I don't..."
                ]
                if any(_re.match(p, after_q) for p in fake_reply_patterns):
                    # Trim the answer to just up to and including the "?"
                    answer = answer[:q_pos + 1].strip()

    except httpx.ConnectError:
        answer = (
            "⚠️ **Ollama is not running.**\n\n"
            "Please start it in a terminal:\n```\nollama serve\n```\n\n"
            f"Then make sure the model is available:\n```\nollama pull {MODEL_NAME}\n```"
        )
    except httpx.ReadTimeout:
        answer = "⚠️ AI is taking too long. The model might be loading — please wait a moment and try again."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            answer = f"⚠️ Model `{MODEL_NAME}` not found.\n\nRun: `ollama pull {MODEL_NAME}`"
        else:
            answer = f"⚠️ Ollama error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        answer = f"⚠️ Unexpected error: {str(e)}"

    log_event("rag_query")
    return {
        "answer":         answer,
        "sources":        sources,
        "profile":        profile,
        "completeness":   completeness,
        "profile_summary": profile_summary,
        "model":          MODEL_NAME,
        "contradictions": [],
    }


@app.get("/rag/status")
def rag_status():
    ollama_ok = False; models = []
    try:
        r = httpx.get(OLLAMA_TAGS, timeout=5.0)
        if r.status_code == 200:
            ollama_ok = True
            models = [m["name"] for m in r.json().get("models",[])]
    except Exception: pass
    return {
        "ready": _rag_ready, "chunks": len(_rag_chunks),
        "ai_backend": "ollama", "model": MODEL_NAME,
        "ollama_url": OLLAMA_URL, "ollama_running": ollama_ok,
        "model_pulled": any(MODEL_NAME in m for m in models),
        "available_models": models,
    }

@app.post("/rag/rebuild")
def rag_rebuild(background_tasks: BackgroundTasks):
    background_tasks.add_task(build_rag)
    return {"message": "RAG rebuild started"}


# ─────────────────────────────────────────────────────────────────────────────
# 6. CONTRADICTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_facts(text: str) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    t = text.lower()
    m = re.search(r'(\d{1,2})\s*(?:to|-)\s*(\d{2,3})\s*years?', text, re.I)
    if m: facts["age_min"]=int(m.group(1)); facts["age_max"]=int(m.group(2))
    m = re.search(r'(?:below|upto?|not exceed|less than)\s*(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|crore)?', text, re.I)
    if m:
        num=float(m.group(1).replace(",",""))
        unit=(m.group(2) or "").lower()
        if unit=="lakh": num*=100000
        elif unit=="crore": num*=10000000
        elif num<500: num*=100000
        facts["max_income"]=int(num)
    m = re.search(r'(?:₹|rs\.?)\s*([\d,]+\.?\d*)\s*(lakh|crore)?', text, re.I)
    if m:
        num=float(m.group(1).replace(",",""))
        unit=(m.group(2) or "").lower()
        if unit=="lakh": num*=100000
        elif unit=="crore": num*=10000000
        facts["benefit"]=int(num)
    if "scheduled caste" in t and "scheduled tribe" in t: facts["caste"]="SC/ST"
    elif "scheduled caste" in t: facts["caste"]="SC"
    elif "scheduled tribe" in t: facts["caste"]="ST"
    elif "obc" in t: facts["caste"]="OBC"
    if re.search(r'\b(women only|for women|female only|mahila)\b', t): facts["gender"]="Female"
    return facts

@app.get("/contradictions")
def get_contradictions(limit: int = 50):
    if not ARCHIVE_CSV.exists(): return {"contradictions":[],"error":"archive CSV not found"}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    with open(str(ARCHIVE_CSV), "r", encoding="utf-8-sig", errors="ignore") as f:
        for row in csv.DictReader(f):
            try:
                title = (row.get("scheme_name") or "").strip().lstrip('\ufeff"').rstrip('"')
                if not title: continue
                _slug_raw: str = str(row.get("slug") or title)
                slug  = _slug_raw[0:60].strip()  # type: ignore[index]
                level = (row.get("level") or "Central").strip()
                elig  = (row.get("eligibility") or "").strip()
                ben   = (row.get("benefits") or "").strip()
                combined = f"{row.get('details','')} {elig} {ben}"
                st = _state_from_text(combined) if level.lower() != "central" else "Central"
                norm = re.sub(r"\s+"," ",title.lower().strip())
                groups.setdefault(norm,[]).append({
                    "file":slug,"title":title,"state":st,
                    "parsed":_parse_facts(elig+" "+ben)
                })
            except Exception: pass
    results: List[Dict[str, Any]] = []
    for norm,group in groups.items():
        if len(group)<2: continue
        conflicts: List[Dict[str, Any]] = []
        for i in range(len(group)):
            for j in range(i+1,len(group)):
                a: Dict[str, Any] = group[i].get("parsed", {}) # type: ignore
                b: Dict[str, Any] = group[j].get("parsed", {}) # type: ignore
                for field in ["age_min","age_max","max_income","benefit","caste","gender"]:
                    if field in a and field in b and a[field]!=b[field]:  # type: ignore
                        conflicts.append({"field":field,"value_a":str(a[field]),"value_b":str(b[field]),  # type: ignore
                                          "source_a":group[i]["file"],"source_b":group[j]["file"],  # type: ignore
                                          "state_a":group[i]["state"],"state_b":group[j]["state"]})  # type: ignore
        if conflicts: results.append({"scheme_name":group[0]["title"],"versions":len(group),"conflicts":conflicts})
    results.sort(key=lambda x:-len(x["conflicts"]))
    return {"total":len(results),"contradictions":results[0:limit]}  # type: ignore[index]

@app.get("/contradictions/scheme")
def scheme_contradictions(title: str = Query(...)):
    if not ARCHIVE_CSV.exists(): return {"contradictions":[]}
    group: List[Dict[str, Any]] = []; norm_q=title.lower().strip()
    with open(str(ARCHIVE_CSV), "r", encoding="utf-8-sig", errors="ignore") as f:
        for row in csv.DictReader(f):
            try:
                t = (row.get("scheme_name") or "").strip().lstrip('\ufeff"').rstrip('"')
                if norm_q not in t.lower(): continue
                _slug_raw2: str = str(row.get("slug") or t)
                slug  = _slug_raw2.strip()[0:60]  # type: ignore[index]
                level = (row.get("level") or "Central").strip()
                elig  = (row.get("eligibility") or "").strip()
                ben   = (row.get("benefits") or "").strip()
                combined = f"{row.get('details','')} {elig} {ben}"
                st = _state_from_text(combined) if level.lower() != "central" else "Central"
                group.append({"file":slug,"title":t,"state":st,
                              "parsed":_parse_facts(elig+" "+ben)})
            except Exception: pass
    if len(group)<2: return {"scheme_title":title,"versions_found":len(group),"contradictions":[]}
    conflicts: List[Dict[str, Any]] = []
    for i in range(len(group)):
        for j in range(i+1,len(group)):
            a: Dict[str, Any] = group[i].get("parsed", {}) # type: ignore
            b: Dict[str, Any] = group[j].get("parsed", {}) # type: ignore
            for field in ["age_min","age_max","max_income","benefit","caste","gender"]:
                if field in a and field in b and a[field]!=b[field]:  # type: ignore
                    conflicts.append({"field":field,"value_a":str(a[field]),"value_b":str(b[field]),  # type: ignore
                                      "source_a":group[i].get("file"),"source_b":group[j].get("file")})  # type: ignore
    return {"scheme_title":title,"versions_found":len(group),
            "contradictions":conflicts,"has_conflict":len(conflicts)>0}

# ─────────────────────────────────────────────────────────────────────────────
# 7. OCR
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/ocr")
async def ocr_doc(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"): raise HTTPException(400,"Only image files accepted")
    try:
        import pytesseract # type: ignore
        from PIL import Image, ImageFilter, ImageEnhance # type: ignore
    except ImportError: raise HTTPException(500,"Run: pip install pytesseract Pillow")
    img_bytes=await file.read()
    img=Image.open(io.BytesIO(img_bytes)).convert("L")
    img=ImageEnhance.Contrast(img).enhance(2.0); img=img.filter(ImageFilter.SHARPEN)
    text=""
    for lang in ["eng+hin+guj","eng+hin","eng"]:
        try: text=pytesseract.image_to_string(img,lang=lang); break
        except Exception: pass
    if not text: text=pytesseract.image_to_string(img)
    result: Dict[str, Any] = {"name":None,"age":None,"dob":None,"gender":None,"state":None,"district":None,"aadhaar":None,"pan":None}
    m=re.search(r'\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b',text)
    if m: result["aadhaar"]=re.sub(r'[\s\-]','-',m.group(1))
    m=re.search(r'\b([A-Z]{5}[0-9]{4}[A-Z])\b',text)
    if m: result["pan"]=m.group(1)
    m=re.search(r'(?:DOB|Date of Birth|जन्म)[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',text,re.I)
    if m:
        result["dob"]=m.group(1)
        try: result["age"]=datetime.now().year-int(getattr(re.search(r'\d{4}',getattr(m, "group", lambda x: "0")(1)), "group", lambda: "0")()) # type: ignore
        except Exception: pass
    if re.search(r'\bFEMALE\b',text,re.I): result["gender"]="Female"
    elif re.search(r'\bMALE\b',text,re.I): result["gender"]="Male"
    for line_raw in str(text).split("\n"):
        line_str = str(line_raw).strip()
        if (re.match(r'^[A-Za-z ]{5,40}$',line_str) and len(line_str.split())>=2 and
                not any(k in line_str.upper() for k in ["GOVT","INDIA","AADHAAR","UNIQUE","AUTHORITY"])):
            result["name"]=line_str.title(); break
    for s in ["Gujarat","Maharashtra","Rajasthan","Uttar Pradesh","Bihar",
              "West Bengal","Tamil Nadu","Karnataka","Kerala","Andhra Pradesh","Madhya Pradesh","Punjab","Haryana"]:
        if s.lower() in str(text).lower(): result["state"]=s; break
    return result

# ─────────────────────────────────────────────────────────────────────────────
# 8. USER PROFILE
# ─────────────────────────────────────────────────────────────────────────────

PROFILES_DIR = BASE_DIR / "user_data" / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

def _load_profile(sid: str) -> dict:
    p = PROFILES_DIR / f"{sid}.json"
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
    return {"session_id": sid, "merged": {}}

def _save_profile(sid: str, data: dict):
    (PROFILES_DIR/f"{sid}.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

@app.post("/user/save-doc")
async def save_doc(session_id: Optional[str]=Query(default=None), file: UploadFile=File(...)):
    sid_str: str = str(session_id) if session_id else str(uuid.uuid4())
    sid = sid_str[0:12]  # type: ignore[index]
    if not file.content_type.startswith("image/"): raise HTTPException(400,"Only image files accepted")
    try:
        import pytesseract # type: ignore
        from PIL import Image, ImageEnhance # type: ignore
        img_bytes=await file.read()
        img=Image.open(io.BytesIO(img_bytes)).convert("L"); img=ImageEnhance.Contrast(img).enhance(2.0)
        text=""
        for lang in ["eng+hin+guj","eng+hin","eng"]:
            try: text=pytesseract.image_to_string(img,lang=lang); break
            except Exception: pass
        if not text: text=pytesseract.image_to_string(img)
    except ImportError: return {"session_id":sid,"error":"pytesseract not installed"}
    ocr: dict={}
    m=re.search(r'\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b',text)
    if m: ocr["aadhaar"]=re.sub(r'[\s\-]','-',m.group(1))
    m=re.search(r'(?:DOB|Date of Birth)[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',text,re.I)
    if m:
        ocr["dob"]=m.group(1)
        try:
            yob_m = re.search(r'\d{4}',m.group(1))
            if yob_m: ocr["age"] = datetime.now().year - int(yob_m.group())
        except Exception: pass
    if re.search(r'\bFEMALE\b',text,re.I): ocr["gender"]="Female"
    elif re.search(r'\bMALE\b',text,re.I): ocr["gender"]="Male"
    for line_raw in str(text).split("\n"):
        line_str = str(line_raw).strip()
        if (re.match(r'^[A-Za-z ]{5,40}$',line_str) and len(line_str.split())>=2 and
                not any(k in line_str.upper() for k in ["GOVT","INDIA","AADHAAR","UNIQUE"])):
            ocr["name"]=line_str.title(); break
    for s in ["Gujarat","Maharashtra","Rajasthan","Uttar Pradesh","Bihar","West Bengal",
              "Tamil Nadu","Karnataka","Kerala","Andhra Pradesh","Madhya Pradesh"]:
        if s.lower() in str(text).lower(): ocr["state"]=s; break
    profile=_load_profile(sid); profile["merged"].update({k:v for k,v in ocr.items() if v})
    _save_profile(sid,profile)
    return {"session_id":sid,"ocr_extracted":ocr,"merged_profile":profile["merged"]}

@app.get("/user/profile/{session_id}")
def get_profile(session_id: str): return _load_profile(session_id)

class ProfileUpdate(BaseModel):
    name: Optional[str]=None; age: Optional[int]=None; gender: Optional[str]=None
    state: Optional[str]=None; district: Optional[str]=None; caste: Optional[str]=None
    income: Optional[int]=None; bpl: Optional[bool]=None; categories: Optional[list]=None

@app.post("/user/update/{session_id}")
def update_profile(session_id: str, data: ProfileUpdate):
    profile=_load_profile(session_id)
    for k,v in data.dict(exclude_none=True).items():
        if v not in (None,"",[]):profile["merged"][k]=v
    _save_profile(session_id,profile)
    return {"session_id":session_id,"merged":profile["merged"]}

class NLReq(BaseModel):
    text: str; language: Optional[str]="en"; session_id: Optional[str]=None

@app.post("/user/parse-nl")
def parse_nl(req: NLReq):
    # Use the improved extract_profile_from_history for consistency
    parsed = extract_profile_from_history([{"role":"user","content":req.text}])
    if req.session_id:
        sid: str = str(req.session_id)
        p = _load_profile(sid); p["merged"].update(parsed); _save_profile(sid, p)
    return {"parsed":parsed,"session_id":req.session_id}

# ─────────────────────────────────────────────────────────────────────────────
# 9. VOICE
# ─────────────────────────────────────────────────────────────────────────────

class TTSReq(BaseModel):
    text: str; language: str="en"

@app.post("/voice/tts")
async def tts(req: TTSReq):
    return {"success":False,"use_browser_tts":True,"fallback_text":req.text,"language":req.language}

@app.post("/voice/stt")
async def stt(data: dict):
    return {"transcript":"","success":False,"use_browser_stt":True}

@app.get("/voice/languages")
def languages():
    return {"languages":[
        {"code":"en","name":"English"},{"code":"hi","name":"हिन्दी"},
        {"code":"gu","name":"ગુજરાતી"},{"code":"mr","name":"मराठी"},
        {"code":"ta","name":"தமிழ்"},{"code":"te","name":"తెలుగు"},
        {"code":"bn","name":"বাংলা"},{"code":"kn","name":"ಕನ್ನಡ"},
        {"code":"ml","name":"മലയാളം"},{"code":"pa","name":"ਪੰਜਾਬੀ"},
        {"code":"or","name":"ଓଡ଼ିଆ"},{"code":"ur","name":"اردو"},
    ],"default":"en"}

@app.post("/translate")
async def translate(data: dict): return {"translated":data.get("text",""),"success":False}

@app.get("/i18n/{key}")
def i18n(key: str, lang: str="en"): return {"key":key,"lang":lang,"text":key}

# ─────────────────────────────────────────────────────────────────────────────
# 10. ADMIN ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/admin/analytics")
def analytics():
    conn=get_db()
    total=conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0]
    adm=conn.execute("SELECT COUNT(*) FROM admin_schemes").fetchone()[0]
    searches=conn.execute("SELECT COUNT(*) FROM events WHERE event_type='search'").fetchone()[0]
    views=conn.execute("SELECT COUNT(*) FROM events WHERE event_type='view'").fetchone()[0]
    rag_q=conn.execute("SELECT COUNT(*) FROM events WHERE event_type='rag_query'").fetchone()[0]
    top=rows(conn.execute("SELECT scheme_title,COUNT(*) as views,category FROM events WHERE event_type='view' AND scheme_title IS NOT NULL GROUP BY scheme_title ORDER BY views DESC LIMIT 10").fetchall())
    by_state=rows(conn.execute("SELECT user_state,COUNT(*) as searches FROM events WHERE user_state IS NOT NULL GROUP BY user_state ORDER BY searches DESC LIMIT 12").fetchall())
    by_cat=rows(conn.execute("SELECT category,COUNT(*) as views FROM events WHERE event_type='view' AND category IS NOT NULL GROUP BY category ORDER BY views DESC").fetchall())
    daily=rows(conn.execute("SELECT DATE(ts) as day,COUNT(*) as events FROM events WHERE ts >= datetime('now','-14 days') GROUP BY day ORDER BY day").fetchall())
    conn.close()
    return {"total_schemes":total+adm,"total_searches":searches,"total_views":views,
            "total_rag_queries":rag_q,"top_schemes":top,"by_state":by_state,
            "by_category":by_cat,"daily_activity":daily,
            "rag_status":{"ready":_rag_ready,"chunks":len(_rag_chunks)}}

# ─────────────────────────────────────────────────────────────────────────────
# 11. ADMIN SCHEME MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

class NewScheme(BaseModel):
    title: str; category: str; state: str="Central"
    min_age: Optional[int]=None; max_age: Optional[int]=None
    max_income: Optional[int]=None; caste: str="All"; gender: str="All"
    benefit_text: Optional[str]=None; eligibility: Optional[str]=None
    documents: Optional[str]=None; apply_process: Optional[str]=None; details: Optional[str]=None

@app.post("/admin/schemes")
def add_scheme(s: NewScheme, background_tasks: BackgroundTasks):
    conn=get_db()
    conn.execute("""INSERT INTO admin_schemes
        (title,category,state,min_age,max_age,max_income,caste,gender,
         benefit_text,eligibility,documents,apply_process,details)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (s.title,s.category,s.state,s.min_age,s.max_age,s.max_income,
         s.caste,s.gender,s.benefit_text,s.eligibility,s.documents,s.apply_process,s.details))
    conn.commit(); new_id=conn.execute("SELECT last_insert_rowid()").fetchone()[0]; conn.close()
    def rebuild_rag_task():
        build_rag()
    background_tasks.add_task(rebuild_rag_task)
    return {"success":True,"id":new_id}

@app.get("/admin/schemes")
def list_admin():
    conn=get_db(); r=rows(conn.execute("SELECT * FROM admin_schemes ORDER BY created_at DESC").fetchall()); conn.close(); return r

@app.delete("/admin/schemes/{scheme_id}")
def delete_admin(scheme_id: int):
    conn=get_db(); conn.execute("DELETE FROM admin_schemes WHERE id=?",(scheme_id,)); conn.commit(); conn.close(); return {"success":True}

# ─────────────────────────────────────────────────────────────────────────────
# 12. CONVERSATIONAL AI CHAT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str   # 'user' | 'assistant'
    content: str

class ChatReq(BaseModel):
    message:  str
    history:  Optional[List[ChatMessage]] = []
    profile:  Optional[dict] = {}
    language: Optional[str] = "en"


# ── Smart profile extractor ───────────────────────────────────────────────────

def _extract_profile(text: str) -> dict:
    """
    Extract structured profile fields from any natural language text.
    Understands Indian-specific implied facts.
    """
    p: dict = {}
    t = text.lower()

    # ── Age ──
    m = re.search(r'(\d{1,3})\s*(?:year|yr|sal|saal)s?\s*old', t)
    if not m: m = re.search(r'(?:i am|aged?|age[:\s])\s*(\d{1,3})', t)
    if m:
        age = int(m.group(1))
        if 1 < age < 120: p["age"] = age

    # ── State ──
    for s in ["Gujarat","Maharashtra","Rajasthan","Uttar Pradesh","Bihar",
              "West Bengal","Tamil Nadu","Karnataka","Kerala","Andhra Pradesh",
              "Madhya Pradesh","Punjab","Haryana","Assam","Odisha","Jharkhand",
              "Delhi","Telangana","Chhattisgarh","Uttarakhand","Himachal Pradesh",
              "Goa","Manipur","Meghalaya","Mizoram","Nagaland","Arunachal Pradesh",
              "Sikkim","Tripura","Jammu and Kashmir"]:
        if s.lower() in t:
            p["state"] = s
            break

    # ── Income ──
    m = re.search(r'(?:income|earn|salary|kamai)[^\d₹]*(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|lac|thousand)?', t)
    if not m: m = re.search(r'(?:₹|rs\.?)\s*([\d,]+\.?\d*)\s*(lakh|lac|thousand)?\s*(?:per\s*year|annual|pa|yearly)', t)
    if m:
        num = float(m.group(1).replace(',', ''))
        unit = (m.group(2) or '').lower()
        if 'lakh' in unit or 'lac' in unit: num *= 100_000
        elif 'thousand' in unit: num *= 1_000
        elif num < 500: num *= 100_000
        if num > 0: p["income"] = int(num)

    # ── BPL ──
    if re.search(r'\b(bpl|below poverty|ration card|garib|very poor)\b', t):
        p["bpl"] = True
        if not p.get("income"): p["income"] = 50000   # imply low income

    # ── Caste ──
    if re.search(r'\bsc/st\b|scheduled caste.*scheduled tribe|scheduled tribe.*scheduled caste', t):
        p["caste"] = "SC/ST"
    elif re.search(r'scheduled caste|\bsc\b|dalit|harijan', t):
        p["caste"] = "SC"
    elif re.search(r'scheduled tribe|\bst\b|adivasi|tribal', t):
        p["caste"] = "ST"
    elif re.search(r'\bobc\b|other backward|backward class', t):
        p["caste"] = "OBC"
    elif re.search(r'\bgeneral\b|open category|unreserved', t):
        p["caste"] = "General"
    elif re.search(r'\bminority\b|muslim|christian|sikh|buddhist|jain', t):
        p["caste"] = "Minority"

    # ── Gender + implied facts ──
    if re.search(r'\b(widow|widowed|i am a widow|my husband died|husband passed)\b', t):
        p["gender"] = "Female"
        p["marital_status"] = "widow"
        p.setdefault("categories", []).extend(["Women & Child", "Social Security"])
    elif re.search(r'\b(woman|female|mahila|girl|beti|daughter|mother|single mother|divorcee|separated)\b', t):
        p["gender"] = "Female"
    elif re.search(r'\b(man|male|father|husband|he is)\b', t):
        p["gender"] = "Male"

    # ── Occupation + implied categories ──
    cats = p.get("categories", [])
    if re.search(r'\b(farmer|kisan|kisaan|agriculture|kheti|farming|crops|khet)\b', t):
        p["occupation"] = "Farmer"
        if "Agriculture" not in cats: cats.append("Agriculture")
    elif re.search(r'\b(student|studying|school|college|class [0-9]|std [0-9]|padhai)\b', t):
        p["occupation"] = "Student"
        if "Education" not in cats: cats.append("Education")
    elif re.search(r'\b(unemployed|no job|jobless|berojgar|looking for work)\b', t):
        p["occupation"] = "Unemployed"
        if "Employment" not in cats: cats.append("Employment")
    elif re.search(r'\b(labourer|labor|mazdoor|worker|daily wage|construction worker)\b', t):
        p["occupation"] = "Daily Wage Worker"
    elif re.search(r'\b(small business|shopkeeper|self.?employed|dukan|vyapar)\b', t):
        p["occupation"] = "Small Business Owner"
        if "Employment" not in cats: cats.append("Employment")

    # ── Disability ──
    if re.search(r'\b(disabled|disability|divyang|handicap|physically challenged|blind|deaf)\b', t):
        p["disability"] = True
        if "Social Security" not in cats: cats.append("Social Security")

    # ── Specific needs ──
    if re.search(r'\b(house|home|housing|ghar|makan|shelter|awas)\b', t):
        if "Housing" not in cats: cats.append("Housing")
    if re.search(r'\b(health|medical|hospital|treatment|sick|bimari|cancer|diabetes)\b', t):
        if "Health" not in cats: cats.append("Health")
    if re.search(r'\b(pension|old age|senior citizen|retirement|aged|60 year|65 year|70 year)\b', t):
        if "Social Security" not in cats: cats.append("Social Security")
    if re.search(r'\b(education|scholarship|school fees|college fees|tuition|padhai)\b', t):
        if "Education" not in cats: cats.append("Education")
    if re.search(r'\b(business|loan|startup|employment|job|rojgar|self.?employ)\b', t):
        if "Employment" not in cats: cats.append("Employment")

    if cats: p["categories"] = list(dict.fromkeys(cats))  # dedup preserving order
    return p


def _profile_summary(profile: dict) -> str:
    """Human-readable profile summary for the AI prompt."""
    if not profile: return "Nothing known yet."
    bits = []
    if profile.get('age'):         bits.append(f"Age {profile['age']}")
    if profile.get('state'):       bits.append(f"from {profile['state']}")
    if profile.get('gender'):      bits.append(profile['gender'])
    if profile.get('occupation'):  bits.append(profile['occupation'])
    if profile.get('caste'):       bits.append(f"{profile['caste']} category")
    if profile.get('income'):      bits.append(f"income ~₹{profile['income']:,}/year")
    if profile.get('bpl'):         bits.append("BPL card holder")
    if profile.get('disability'):  bits.append("person with disability")
    if profile.get('marital_status'): bits.append(profile['marital_status'])
    if profile.get('categories'):  bits.append("needs: " + ", ".join(profile['categories']))
    return " | ".join(bits)


def _profile_completeness(profile: dict) -> int:
    """0-100 score of how complete the profile is for scheme matching."""
    score = 0
    if profile.get('state'):      score += 35   # most important — many schemes are state-specific
    if profile.get('age'):        score += 20
    if profile.get('income') or profile.get('bpl'): score += 20
    if profile.get('caste'):      score += 10
    if profile.get('gender'):     score += 5
    if profile.get('occupation'): score += 5
    if profile.get('categories'): score += 5
    return score


# ── Conversational AI endpoint ────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatReq):
    """
    Primary conversational AI engine.
    Builds citizen profile through natural dialogue.
    Returns {answer, mode, profile_update} where mode is
    'collecting' (still asking) or 'show_schemes' (ready to display matches).
    """
    profile = req.profile or {}
    history = req.history or []
    language = req.language or "en"

    # Extract profile from all user messages in history + current message
    all_user_text = " ".join(
        m.content for m in history if m.role == "user"
    ) + " " + req.message
    extracted = _extract_profile(all_user_text)

    # Merge extracted into existing profile (existing values win if already set)
    merged_profile = {**extracted, **{k: v for k, v in profile.items() if v}}

    completeness = _profile_completeness(merged_profile)
    profile_summary = _profile_summary(merged_profile)

    lang_instruction = {
        "hi": "IMPORTANT: Reply entirely in simple Hindi (Devanagari). Use easy words a village person understands.",
        "gu": "IMPORTANT: Reply entirely in simple Gujarati. Use easy words.",
        "en": "Reply in simple English. Avoid jargon.",
        "mr": "IMPORTANT: Reply entirely in simple Marathi.",
        "ta": "IMPORTANT: Reply entirely in simple Tamil.",
        "te": "IMPORTANT: Reply entirely in simple Telugu.",
        "bn": "IMPORTANT: Reply entirely in simple Bengali.",
    }.get(language, "Reply in simple English.")

    system_prompt = f"""You are PolicyPilot — a warm, caring AI assistant that helps Indian citizens find government welfare schemes they qualify for. You speak like a helpful, knowledgeable neighbour — not a government officer.

{lang_instruction}

YOUR GOAL: Understand the citizen's situation through natural conversation, then help them find the right schemes.

WHAT YOU KNOW ABOUT THE CITIZEN SO FAR:
{profile_summary}

PROFILE COMPLETENESS: {completeness}/100

HOW TO BEHAVE:
1. Ask ONE follow-up question at a time. Be warm and conversational — not like a form.
2. If completeness < 60, ask for missing key info (state, age, income, or what help they need).
3. If completeness >= 60, OR if the user has given enough context (state + occupation/need), write [SHOW_SCHEMES] on the last line.
4. Understand EVERYTHING implied:
   - "I am a farmer" → they need Agriculture schemes, may qualify for PM-KISAN, crop insurance
   - "I am a widow" → women's schemes, widow pension, children's education support
   - "I have BPL card" → they qualify for most poverty schemes — Ayushman Bharat, PMAY, PDS
   - "I am disabled/divyang" → disability pension, free aids, scholarship schemes
   - "I am a student" → scholarships, education loans, mid-day meal
   - "I am old / 60+ years" → old age pension, Ayushman Bharat, senior schemes
   - "I am SC/ST" → special reservation schemes, scholarship, loan schemes
   - "I need a house" → PMAY housing scheme
   - "I have no job" → employment schemes, MGNREGA, skill development
5. After giving your warm response, if profile is complete enough, end EXACTLY with:
[SHOW_SCHEMES]
6. Do NOT show [SHOW_SCHEMES] if you still need critical info (especially state of residence).
7. When user says hi/hello/namaste — greet warmly, introduce yourself briefly, ask what state they're from.
8. If they ask about a specific scheme by name — answer it directly and add [SHOW_SCHEMES].
9. Keep responses SHORT (2-4 sentences max) unless they ask a specific question.
10. Never say "I cannot help" — always be positive and guide them."""

    # Build conversation for Ollama
    conversation = ""
    for msg in (history or [])[-8:]:   # last 8 messages for context
        role_label = "Citizen" if msg.role == "user" else "PolicyPilot"
        conversation += f"\n{role_label}: {msg.content}"

    full_prompt = (
        f"{system_prompt}"
        f"{conversation}"
        f"\nCitizen: {req.message}"
        f"\nPolicyPilot:"
    )

    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": full_prompt, "stream": False},
            timeout=90.0
        )
        raw = resp.json().get("response", "").strip()
    except httpx.ConnectError:
        return {
            "answer": "⚠️ The AI (Ollama) is not running. Please start it by running:\n  `ollama run phi3:mini`\nin a terminal window.",
            "mode": "error",
            "profile_update": extracted,
            "show_schemes": False,
        }
    except Exception as e:
        return {
            "answer": f"⚠️ AI error: {str(e)}",
            "mode": "error",
            "profile_update": extracted,
            "show_schemes": False,
        }

    # Parse [SHOW_SCHEMES] signal
    show_schemes = "[SHOW_SCHEMES]" in raw
    clean_answer = raw.replace("[SHOW_SCHEMES]", "").strip()

    # Log
    log_event("chat", user_state=merged_profile.get("state"), user_age=merged_profile.get("age"))

    return {
        "answer":         clean_answer,
        "mode":           "show_schemes" if show_schemes else "collecting",
        "profile_update": extracted,
        "show_schemes":   show_schemes,
        "completeness":   completeness,
    }


# ── Scheme detail via RAG (used by Ask AI button on scheme cards) ─────────────

class SchemeDetailReq(BaseModel):
    scheme_title: str
    scheme_data:  Optional[dict] = {}
    question:     Optional[str] = ""
    language:     Optional[str] = "en"

@app.post("/scheme/ask")
async def scheme_ask(req: SchemeDetailReq):
    """
    Answer a question about a specific scheme.
    Uses the scheme data we already have + RAG retrieval.
    This powers the 'Ask AI' button on scheme cards.
    """
    s = req.scheme_data or {}
    question = req.question or f"Tell me everything about the {req.scheme_title} scheme in simple language."

    # Build rich context from the scheme card data
    scheme_context = f"""
SCHEME NAME: {s.get('title', req.scheme_title)}
STATE: {s.get('state', 'Central')}
CATEGORY: {s.get('category', '')}
BENEFITS: {(s.get('benefit_text') or 'Not specified')[:600]}
ELIGIBILITY: {(s.get('eligibility') or 'Not specified')[:600]}
DOCUMENTS REQUIRED: {(s.get('documents') or 'Not specified')[:400]}
HOW TO APPLY: {(s.get('apply_process') or 'Not specified')[:400]}
DETAILS: {(s.get('details') or '')[:300]}
""".strip()

    # Also retrieve additional context from RAG
    extra_chunks = rag_retrieve(req.scheme_title, top_k=4)
    if extra_chunks:
        extra_context = "\n\n" + "\n---\n".join(
            f"[From {c.fname}, {c.section}]:\n{c.text[:400]}" for c in extra_chunks
        )
    else:
        extra_context = ""

    lang_inst = {
        "hi": "Answer entirely in simple Hindi.",
        "gu": "Answer entirely in simple Gujarati.",
        "en": "Answer in simple English.",
    }.get(req.language or "en", "Answer in simple English.")

    prompt = f"""You are PolicyPilot. {lang_inst}

Answer the citizen's question about this government scheme. Be specific, clear, and practical.
Break your answer into sections: Who can apply | What benefit they get | What documents needed | How to apply step by step.
If you mention a step, make it actionable (e.g. "Go to pmkisan.gov.in and click New Farmer Registration").
Cite the source at the end.

SCHEME INFORMATION:
{scheme_context}
{extra_context}

CITIZEN'S QUESTION: {question}

ANSWER:"""

    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=90.0
        )
        answer = resp.json().get("response", "Could not generate response.").strip()
    except httpx.ConnectError:
        answer = "⚠️ AI offline. Run: `ollama run phi3:mini`"
    except Exception as e:
        answer = f"⚠️ Error: {e}"

    log_event("scheme_ask", scheme_title=req.scheme_title)
    return {
        "answer":  answer,
        "sources": [{"file": c.fname, "title": c.title, "section": c.section} for c in extra_chunks],
    }
