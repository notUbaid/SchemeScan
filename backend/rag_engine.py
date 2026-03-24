"""
rag_engine.py — SchemeScan RAG + Contradiction Detection Engine
----------------------------------------------------------------
• BM25-based offline retrieval over archive/updated_data.csv
• Source-cited answers via Ollama (phi3:mini)
• Contradiction detection: compares scheme pairs with the same title,
  finds conflicting age / income / benefit / caste / gender criteria
  and returns exact source slug citations for each conflict.
"""

import os, re, csv, json, math
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import httpx # type: ignore

ARCHIVE_CSV = Path(__file__).parent.parent / "archive" / "updated_data.csv"
OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL_NAME  = "phi3:mini"


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

class SchemeChunk:
    """A single section of a scheme record, the atomic unit for RAG retrieval."""
    def __init__(self, file_name: str, title: str, section: str,
                 text: str, state: str):
        self.file_name = file_name
        self.title     = title
        self.section   = section
        self.text      = text
        self.state     = state

    def to_dict(self) -> Dict:
        return {
            "file":    self.file_name,
            "title":   self.title,
            "section": self.section,
            "state":   self.state,
            "excerpt": str(self.text)[:400], # type: ignore
        }

    def full_context(self) -> str:
        return (f"[SOURCE: {self.file_name}  |  SCHEME: {self.title}"
                f"  |  SECTION: {self.section}  |  STATE: {self.state}]\n"
                f"{str(self.text)[:700]}") # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
# CSV LOADING HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _load_csv_rows() -> List[Dict]:
    """Load all rows from updated_data.csv into a list of dicts."""
    if not ARCHIVE_CSV.exists():
        return []
    rows = []
    with open(str(ARCHIVE_CSV), "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _guess_state(text: str, level: str = "") -> str:
    """Detect actual Indian state name from text; fall back to Central."""
    if level.strip().lower() == "central":
        return "Central"
    STATES = [
        "Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh",
        "Goa","Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka",
        "Kerala","Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram",
        "Nagaland","Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu",
        "Telangana","Tripura","Uttar Pradesh","Uttarakhand","West Bengal",
        "Delhi","Chandigarh","Puducherry","Lakshadweep","Jammu and Kashmir",
        "Ladakh","Andaman",
    ]
    for s in STATES:
        if re.search(r'\b' + re.escape(s) + r'\b', text, re.I):
            return s
    return "Central"


# ══════════════════════════════════════════════════════════════════════════════
# TEXT PARSING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_eligibility_facts(text: str) -> Dict:
    """
    Extract structured numeric / categorical facts from eligibility + benefit text.
    Used for deterministic contradiction comparison.
    """
    facts: Dict[str, Any] = {}
    text_lower = text.lower()

    # ── Age ──────────────────────────────────────────────────────────────────
    m = re.search(r'(\d{1,2})\s*(?:to|–|-)\s*(\d{2,3})\s*years?', text, re.I)
    if m:
        facts["age_min"] = int(m.group(1))
        facts["age_max"] = int(m.group(2))
    else:
        m = re.search(r'above\s+(\d{1,2})\s*years?', text, re.I)
        if m: facts["age_min"] = int(m.group(1))
        m = re.search(r'(?:below|under|not.*?exceed)\s+(\d{1,3})\s*years?', text, re.I)
        if m: facts["age_max"] = int(m.group(1))

    # ── Income ───────────────────────────────────────────────────────────────
    m = re.search(
        r'(?:below|upto?|not exceed|less than|within|maximum.*?income)\s*'
        r'(?:rs\.?|₹)?\s*([\d,]+\.?\d*)\s*(lakh|crore|thousand)?',
        text, re.I
    )
    if m:
        num  = float(m.group(1).replace(',', ''))
        unit = (m.group(2) or '').lower()
        if   unit == 'lakh':     num *= 100_000
        elif unit == 'crore':    num *= 10_000_000
        elif unit == 'thousand': num *= 1_000
        elif num < 500:          num *= 100_000
        facts["max_income"] = int(num)

    # ── Benefit / Subsidy amount ──────────────────────────────────────────────
    m = re.search(r'(?:₹|rs\.?)\s*([\d,]+\.?\d*)\s*(lakh|crore|thousand)?', text, re.I)
    if m:
        num  = float(m.group(1).replace(',', ''))
        unit = (m.group(2) or '').lower()
        if   unit == 'lakh':  num *= 100_000
        elif unit == 'crore': num *= 10_000_000
        facts["benefit_amount"] = int(num)

    # ── Caste ────────────────────────────────────────────────────────────────
    if "sc/st" in text_lower or ("scheduled caste" in text_lower and "scheduled tribe" in text_lower):
        facts["caste"] = "SC/ST"
    elif "scheduled caste" in text_lower or re.search(r'\bsc\b', text_lower):
        facts["caste"] = "SC"
    elif "scheduled tribe" in text_lower or re.search(r'\bst\b', text_lower):
        facts["caste"] = "ST"
    elif "obc" in text_lower or "other backward" in text_lower:
        facts["caste"] = "OBC"
    elif "minority" in text_lower or "minorities" in text_lower:
        facts["caste"] = "Minority"

    # ── Gender ───────────────────────────────────────────────────────────────
    if re.search(r'\b(women only|only women|female only|for women|for girls|mahila|kanya|beti)\b', text_lower):
        facts["gender"] = "Female"
    elif re.search(r'\b(men only|only men|male only)\b', text_lower):
        facts["gender"] = "Male"

    return facts


# ══════════════════════════════════════════════════════════════════════════════
# BM25
# ══════════════════════════════════════════════════════════════════════════════

class SimpleBM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1; self.b = b
        self.corpus: List[List[str]] = []
        self.idf:    Dict[str, float] = {}
        self.avg_dl: float = 0.0

    def fit(self, tokenized_corpus: List[List[str]]):
        self.corpus = tokenized_corpus
        N = len(tokenized_corpus)
        self.avg_dl = sum(len(d) for d in tokenized_corpus) / max(N, 1)
        df: Dict[str, int] = {}
        for doc in tokenized_corpus:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        self.idf = {
            term: math.log((N - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def get_scores(self, query_tokens: List[str]) -> List[float]:
        scores = [0.0] * len(self.corpus)
        for term in query_tokens:
            idf = self.idf.get(term, 0.0)
            for i, doc in enumerate(self.corpus):
                tf = doc.count(term)
                dl = len(doc)
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(self.avg_dl, 1))
                scores[i] += idf * (tf * (self.k1 + 1)) / max(denom, 1e-9)
        return scores


def _tokenize(text: str) -> List[str]:
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return [t for t in text.split() if len(t) > 1]


# ══════════════════════════════════════════════════════════════════════════════
# RAG ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class RAGEngine:
    def __init__(self):
        self.chunks:  List[SchemeChunk] = []
        self._bm25:   Optional[SimpleBM25] = None
        self._ready:  bool = False
        self._count:  int  = 0

    # ── Build index from CSV ──────────────────────────────────────────────────
    def build(self) -> Tuple[bool, str]:
        if not ARCHIVE_CSV.exists():
            return False, f"archive CSV not found at {ARCHIVE_CSV}"

        rows = _load_csv_rows()
        if not rows:
            return False, "No rows found in updated_data.csv"

        self.chunks = []
        SECTIONS = {
            "BENEFITS":            "benefits",
            "ELIGIBILITY":         "eligibility",
            "DOCUMENTS":           "documents",
            "APPLICATION PROCESS": "application",
            "DETAILS":             "details",
        }

        for row in rows:
            title = (row.get("scheme_name") or "").strip().lstrip('\ufeff"').rstrip('"')
            slug  = str((row.get("slug") or title)).strip()[:60] # type: ignore
            level = (row.get("level") or "Central").strip()
            combined = " ".join([
                row.get("details", ""),
                row.get("eligibility", ""),
                row.get("benefits", ""),
            ])
            state = _guess_state(combined, level)

            for section_label, csv_col in SECTIONS.items():
                text = (row.get(csv_col) or "").strip()
                if text and len(text) > 30:
                    self.chunks.append(
                        SchemeChunk(slug, title, section_label, text, state)
                    )

        corpus = [
            _tokenize(f"{c.title} {c.section} {c.text}")
            for c in self.chunks
        ]

        try:
            from rank_bm25 import BM25Okapi # type: ignore
            self._bm25 = BM25Okapi(corpus)
        except ImportError:
            bm25 = SimpleBM25()
            bm25.fit(corpus)
            self._bm25 = bm25

        self._ready = True
        self._count = len(rows)
        return True, f"Indexed {len(self.chunks)} chunks from {len(rows)} CSV rows"

    # ── Retrieve ──────────────────────────────────────────────────────────────
    def retrieve(self, query: str, top_k: int = 8,
                 state_filter: Optional[str] = None) -> List[SchemeChunk]:
        if not self._ready or self._bm25 is None:
            return []

        tokens = _tokenize(query)
        scores: List[float] = list(getattr(self._bm25, "get_scores", lambda x: [])(tokens)) # type: ignore

        if state_filter:
            for i, chunk in enumerate(self.chunks):
                if chunk.state.lower() == state_filter.lower() or chunk.state == "Central":
                    scores[i] *= 1.3

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self.chunks[i] for i in ranked[0:top_k] if scores[i] > 0] # type: ignore

    # ── RAG answer via Ollama ─────────────────────────────────────────────────
    async def rag_answer(self, question: str, language: str = "en",
                         state_filter: Optional[str] = None,
                         user_profile: Optional[Dict] = None) -> Dict:
        if not self._ready:
            ok, msg = self.build()
            if not ok:
                return {"answer": f"RAG index not ready: {msg}", "sources": [], "contradictions": []}

        chunks = self.retrieve(question, top_k=8, state_filter=state_filter)

        if not chunks:
            return {
                "answer": (
                    "I'm sorry, I couldn't find any schemes that closely match your question in my database. "
                    "This can happen if your query is very specific or your state has limited coverage in our data.\n\n"
                    "Try describing yourself in more detail — for example: your age, state, occupation, income, and caste category. "
                    "The more you tell me, the better I can help! 😊\n\n"
                    "You can also search directly at: **myscheme.gov.in** (official Government of India portal)."
                ),
                "sources": [],
                "contradictions": []
            }

        context_parts = [c.full_context() for c in chunks]
        context = "\n\n".join(context_parts)
        inline_contradictions = _check_retrieved_contradictions(chunks)

        contradiction_notice = ""
        if inline_contradictions:
            contradiction_notice = (
                "\n\n⚠️ CONTRADICTION ALERT: The retrieved documents contain conflicting "
                "information. Flag these in your answer:\n"
                + "\n".join(
                    f"  • {c['field']}: "
                    f"{c['source_a']} says {c['value_a']}, "
                    f"but {c['source_b']} says {c['value_b']}"
                    for c in inline_contradictions
                )
            )

        lang_map = {
            "hi": "Respond entirely in simple Hindi (Devanagari script). Use very simple words.",
            "gu": "Respond entirely in simple Gujarati. Use very simple words.",
            "en": "Respond in simple English. Avoid jargon.",
            "mr": "Respond entirely in simple Marathi.",
            "bn": "Respond entirely in simple Bengali.",
            "ta": "Respond entirely in simple Tamil.",
            "te": "Respond entirely in simple Telugu.",
        }
        lang_inst = lang_map.get(language, lang_map["en"])

        profile_context = ""
        if user_profile:
            bits: List[str] = []
            if user_profile.get("name"):   bits.append(str(f"Name: {user_profile['name']}"))
            if user_profile.get("age"):    bits.append(str(f"Age: {user_profile['age']}"))
            if user_profile.get("gender"): bits.append(str(f"Gender: {user_profile['gender']}"))
            if user_profile.get("state"):  bits.append(str(f"State: {user_profile['state']}"))
            if user_profile.get("income"): bits.append(str(f"Income: ₹{user_profile['income']}"))
            if user_profile.get("caste"):  bits.append(str(f"Category: {user_profile['caste']}"))
            if bits:
                profile_context = f"\nUSER PROFILE: {' | '.join(bits)}\n"

        prompt = f"""You are SchemeScan AI — a warm, helpful assistant that explains Indian government schemes in the simplest possible language, like explaining to a farmer or daily-wage worker with no formal education.

{lang_inst}
{profile_context}
USER'S QUESTION: {question}

SCHEME DOCUMENTS RETRIEVED:
{context}

TASK: Give a complete, clear answer using ONLY the information from the scheme documents above.
Structure your answer like this:

✅ **Who can get this scheme?**
(List eligibility criteria in plain language — age, income, caste, state, etc.)

📋 **Documents you need:**
(List every document required — Aadhaar, ration card, bank details, etc.)

📝 **How to apply — step by step:**
(List the exact steps to apply, as simple as possible)

💰 **What you will get:**
(Explain the benefit in simple terms — how much money, what help, etc.)

RULES:
- Use ONLY facts from the SCHEME DOCUMENTS above. Never invent.
- Use simple words. Say "money you get" not "disbursement". Say "poor families" not "BPL category".
- If something is not in the documents, say "Please check at your local government office."
- End your answer with: "💬 Have any questions? Just ask me!"
- Do NOT simulate a user reply or continue the conversation yourself. STOP after your answer.
{contradiction_notice}

ANSWER:"""

        try:
            resp = httpx.post(
                OLLAMA_URL,
                json={
                    "model":  MODEL_NAME,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "top_p":       0.9,
                        "num_predict": 1200,
                        "repeat_penalty": 1.1,
                    }
                },
                timeout=180.0
            )
            resp.raise_for_status()
            answer: str = str(resp.json().get("response", "No response from AI.")).strip()

            # Strip any hallucinated user-continuation after the answer ends
            stop_markers = ["User:", "Citizen:", "Question:", "USER:"]
            for marker in stop_markers:
                pos = int(getattr(answer, "find", lambda x: -1)(marker)) # type: ignore
                if pos != -1 and pos > len(answer) // 2:
                    answer = str(answer)[0:pos].strip() # type: ignore

        except httpx.ConnectError:
            answer = "⚠️ AI (Ollama) is offline. Please run: ollama run phi3:mini"
        except Exception as e:
            answer = f"⚠️ AI error: {str(e)}"

        return {
            "answer":         answer,
            "sources":        [c.to_dict() for c in chunks],
            "contradictions": inline_contradictions,
            "model":          MODEL_NAME,
        }

    def status(self) -> Dict:
        return {
            "ready":      self._ready,
            "chunks":     len(self.chunks),
            "rows":       self._count,
            "model":      MODEL_NAME,
            "ollama_url": OLLAMA_URL,
            "data_source": str(ARCHIVE_CSV),
        }

    def is_ready(self) -> bool:
        return self._ready


# ══════════════════════════════════════════════════════════════════════════════
# CONTRADICTION DETECTION (full CSV scan)
# ══════════════════════════════════════════════════════════════════════════════

def detect_all_contradictions(limit: int = 100) -> Dict:
    """
    Scan ALL CSV rows, group by normalized title,
    find conflicting eligibility/benefit criteria.
    """
    if not ARCHIVE_CSV.exists():
        return {"error": f"archive CSV not found at {ARCHIVE_CSV}", "contradictions": []}

    rows = _load_csv_rows()
    title_groups: Dict[str, List[Dict[str, Any]]] = {}

    for row in rows:
        title = (row.get("scheme_name") or "").strip().lstrip('\ufeff"').rstrip('"')
        if not title:
            continue
        slug  = str((row.get("slug") or title)).strip()[:60] # type: ignore
        level = (row.get("level") or "Central").strip()
        elig  = (row.get("eligibility") or "").strip()
        benefits = (row.get("benefits") or "").strip()
        details  = (row.get("details") or "").strip()
        combined = f"{details} {elig} {benefits}"
        state = _guess_state(combined, level)
        norm_title = re.sub(r'\s+', ' ', title.lower().strip())

        entry = {
            "file":        slug,
            "title":       title,
            "state":       state,
            "eligibility": elig,
            "benefits":    benefits,
            "parsed":      _parse_eligibility_facts(elig + " " + benefits),
        }
        title_groups.setdefault(norm_title, []).append(entry)

    all_contradictions = []
    for norm_title, group in title_groups.items():
        if len(group) < 2:
            continue
        conflicts = _find_conflicts_in_group(group)
        if conflicts:
            all_contradictions.append({
                "scheme_name":    group[0]["title"],
                "versions_count": len(group),
                "sources":        [{"file": e["file"], "state": e["state"]} for e in group],
                "conflicts":      conflicts,
            })

    all_contradictions.sort(key=lambda x: -len(x["conflicts"]))

    return {
        "total_schemes_scanned":  sum(len(g) for g in title_groups.values()),
        "unique_scheme_names":    len(title_groups),
        "schemes_with_conflicts": len(all_contradictions),
        "total_conflicts":        int(sum(len(c["conflicts"]) for c in all_contradictions)), # type: ignore
        "contradictions":         all_contradictions[0:limit], # type: ignore
    }


def detect_scheme_contradictions(scheme_title: str) -> Dict:
    """Check contradictions only for schemes matching a given title."""
    if not ARCHIVE_CSV.exists():
        return {"contradictions": [], "error": "archive CSV not found"}

    norm_query = scheme_title.lower().strip()
    group: List[Dict[str, Any]] = []

    for row in _load_csv_rows():
        title = (row.get("scheme_name") or "").strip().lstrip('\ufeff"').rstrip('"')
        if norm_query not in title.lower():
            continue
        slug  = str((row.get("slug") or title)).strip()[:60] # type: ignore
        level = (row.get("level") or "Central").strip()
        elig  = (row.get("eligibility") or "").strip()
        benefits = (row.get("benefits") or "").strip()
        details  = (row.get("details") or "").strip()
        combined = f"{details} {elig} {benefits}"
        state = _guess_state(combined, level)
        group.append({
            "file":        slug,
            "title":       title,
            "state":       state,
            "eligibility": elig,
            "benefits":    benefits,
            "parsed":      _parse_eligibility_facts(elig + " " + benefits),
        })

    if len(group) < 2:
        return {
            "scheme_title":   scheme_title,
            "versions_found": len(group),
            "contradictions": [],
            "message":        "Only one version found — no contradictions possible.",
        }

    conflicts = _find_conflicts_in_group(group)
    return {
        "scheme_title":   scheme_title,
        "versions_found": len(group),
        "sources":        [{"file": e["file"], "state": e["state"]} for e in group],
        "contradictions": conflicts,
        "has_conflict":   len(conflicts) > 0,
    }


# ── Comparison helpers ────────────────────────────────────────────────────────

def _find_conflicts_in_group(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts = []
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            a, b = group[i], group[j]  # type: ignore
            pa, pb = a.get("parsed", {}), b.get("parsed", {})  # type: ignore

            def add_conflict(field, va, vb):
                conflicts.append({
                    "field":       field,
                    "value_a":     str(va),
                    "value_b":     str(vb),
                    "source_a":    a["file"],
                    "source_b":    b["file"],
                    "state_a":     a["state"],
                    "state_b":     b["state"],
                    "severity":    _severity(field, va, vb),
                    "explanation": _explain_conflict(field, va, vb, a, b),
                })

            if "age_min" in pa and "age_min" in pb and pa["age_min"] != pb["age_min"]:
                add_conflict("Minimum Age", f"{pa['age_min']} years", f"{pb['age_min']} years")
            if "age_max" in pa and "age_max" in pb and pa["age_max"] != pb["age_max"]:
                add_conflict("Maximum Age", f"{pa['age_max']} years", f"{pb['age_max']} years")
            if "max_income" in pa and "max_income" in pb:
                diff_pct = abs(pa["max_income"] - pb["max_income"]) / max(pa["max_income"], pb["max_income"])
                if diff_pct > 0.05:
                    add_conflict("Maximum Annual Income", f"₹{pa['max_income']:,}", f"₹{pb['max_income']:,}")
            if "benefit_amount" in pa and "benefit_amount" in pb:
                diff_pct = abs(pa["benefit_amount"] - pb["benefit_amount"]) / max(pa["benefit_amount"], pb["benefit_amount"])
                if diff_pct > 0.05:
                    add_conflict("Benefit / Subsidy Amount", f"₹{pa['benefit_amount']:,}", f"₹{pb['benefit_amount']:,}")
            if "caste" in pa and "caste" in pb and pa["caste"] != pb["caste"]:
                add_conflict("Caste Eligibility", pa["caste"], pb["caste"])
            if "gender" in pa and "gender" in pb and pa["gender"] != pb["gender"]:
                add_conflict("Gender Restriction", pa["gender"], pb["gender"])

    return conflicts


def _check_retrieved_contradictions(chunks: List[SchemeChunk]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in chunks:
        norm = c.title.lower().strip()
        elig = c.text if c.section in ("ELIGIBILITY", "BENEFITS") else ""
        entry = groups.setdefault(norm, [])
        existing = next((e for e in entry if e["file"] == c.file_name), None)
        if existing:
            existing["elig_text"] += " " + elig
        else:
            entry.append({
                "file":       c.file_name,
                "title":      c.title,
                "state":      c.state,
                "eligibility": elig,
                "benefits":   "",
                "elig_text":  elig,
                "parsed":     {},
            })

    for group in groups.values():
        for e in group:
            e["parsed"] = _parse_eligibility_facts(e.get("elig_text", "") or e["eligibility"])

    all_conflicts = []
    for group in groups.values():
        if len(group) >= 2:
            all_conflicts.extend(_find_conflicts_in_group(group))
    return all_conflicts


def _severity(field: str, va, vb) -> str:
    if field in ("Caste Eligibility", "Gender Restriction"):     return "HIGH"
    if field in ("Minimum Age", "Maximum Age"):                   return "HIGH"
    if field == "Maximum Annual Income":                          return "MEDIUM"
    return "LOW"


def _explain_conflict(field: str, va, vb, a: Dict, b: Dict) -> str:
    return (
        f"The scheme '{a['title']}' has conflicting {field}: "
        f"Entry '{a['file']}' (State: {a['state']}) states {va}, "
        f"but entry '{b['file']}' (State: {b['state']}) states {vb}. "
        f"Please check with the official government office before applying."
    )


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[RAGEngine] = None


def get_rag_engine() -> RAGEngine:
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    if not _engine.is_ready():
        _engine.build()
    return _engine
