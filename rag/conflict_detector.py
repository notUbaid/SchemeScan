import anthropic, json, re
from config import Config
from itertools import combinations

client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

def detect_conflicts(matches: list[dict]) -> list[dict]:
    # Group by scheme name to find central vs state versions
    by_name = {}
    for m in matches:
        name = m['scheme_name'].lower().split('(')[0].strip()
        if name not in by_name:
            by_name[name] = []
        by_name[name].append(m)
    
    for name, group in by_name.items():
        central = [m for m in group if m.get('level') == 'central']
        state = [m for m in group if m.get('level') == 'state']
        
        if central and state:
            for c_scheme in central:
                for s_scheme in state:
                    conflict = check_pair_conflict(c_scheme, s_scheme)
                    if conflict:
                        c_scheme['has_conflict'] = True
                        c_scheme['conflict_detail'] = conflict
                        s_scheme['has_conflict'] = True
                        s_scheme['conflict_detail'] = conflict
    
    for m in matches:
        if 'has_conflict' not in m:
            m['has_conflict'] = False
        if 'conflict_detail' not in m:
            m['conflict_detail'] = None
    
    return matches

def check_pair_conflict(central: dict, state: dict) -> dict | None:
    central_reasoning = json.dumps(central.get('reasoning', []))
    state_reasoning = json.dumps(state.get('reasoning', []))
    
    prompt = f"""
Compare these two government scheme documents for the same scheme 
(central vs state version) and find ANY contradictions.

CENTRAL SCHEME CRITERIA:
{central_reasoning}

STATE SCHEME CRITERIA:
{state_reasoning}

If there are contradictions (different income limits, age limits, 
land limits, category restrictions etc.), return JSON:
{{
  "has_conflict": true,
  "conflict_type": "income_limit/age_limit/category/land/other",
  "central_clause": "exact central scheme criterion",
  "state_clause": "exact state scheme criterion",
  "central_pdf_ref": "PDF name and page",
  "state_pdf_ref": "PDF name and page",
  "explanation": "plain language explanation of the contradiction",
  "recommendation": "what the citizen should do"
}}

If NO contradiction, return: {{"has_conflict": false}}

Return ONLY valid JSON.
"""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = re.sub(r'```json|```', '', response.content[0].text).strip()
        result = json.loads(text)
        return result if result.get('has_conflict') else None
    except:
        return None
