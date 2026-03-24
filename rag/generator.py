import json, re

def extract_citizen_profile(english_text: str, state: str = "") -> dict:
    prompt = f"""
Analyze this text and extract details into JSON format.

Text: "{english_text}"
State hint: "{state}"

You MUST return a JSON object with EXACTLY these keys:
- name (string or null)
- age (integer or null, e.g. 19)
- gender (string or null)
- occupation (string or null)
- annual_income (integer or null, e.g. 200000)
- state (string or null)
- category (string or null, e.g. "General")

Return nothing else but the JSON.
"""
    try:
        from utils.ollama_client import get_ollama_response
        messages = [{"role": "user", "content": prompt}]
        text = get_ollama_response(messages)
        
        # Clean up output in case Ollama adds markdown backticks
        text = re.sub(r'```json|```', '', text).strip()
        
        # Try finding the first '{' and last '}'
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            text = text[start_idx:end_idx+1]
            
        return json.loads(text)
    except Exception as e:
        print(f"Ollama Extraction Error: {e}")
        return {
            "raw_text": english_text, 
            "state": state,
            "annual_income_number": None,
            "age": None,
            "category": None,
            "gender": None
        }

def generate_scheme_matches(profile: dict, chunks: list[dict]) -> list[dict]:
    
    # Group chunks by scheme
    scheme_chunks = {}
    for chunk in chunks:
        sid = chunk['metadata'].get('scheme_id', 'unknown')
        if sid not in scheme_chunks:
            scheme_chunks[sid] = {
                'scheme_id': sid,
                'scheme_name': chunk['metadata'].get('scheme_name', ''),
                'level': chunk['metadata'].get('level', ''),
                'state': chunk['metadata'].get('state', ''),
                'category': chunk['metadata'].get('category', ''),
                'chunks': []
            }
        scheme_chunks[sid]['chunks'].append(chunk)
    
    matches = []
    
    for scheme_id, scheme_data in scheme_chunks.items():
        chunks_text = "\n\n".join([
            f"[Section: {c['metadata'].get('section','')}, "
            f"Page {c['metadata'].get('page','?')}, "
            f"PDF: {c['metadata'].get('scheme_name','')}]\n{c['text']}"
            for c in scheme_data['chunks']
        ])
        
        prompt = f"""
You are a government scheme eligibility expert. Evaluate if this citizen 
qualifies for this scheme. Be strict — only mark eligible if criteria 
are clearly met.

CITIZEN PROFILE:
{json.dumps(profile, indent=2)}

SCHEME DOCUMENTS:
{chunks_text}

Return ONLY a valid JSON object:
{{
  "scheme_id": "{scheme_id}",
  "scheme_name": "{scheme_data['scheme_name']}",
  "ministry": "extract from documents or null",
  "level": "{scheme_data['level']}",
  "state": "{scheme_data['state']}",
  "category": "{scheme_data['category']}",
  "eligible": true/false,
  "confidence": "high/medium/low",
  "reasoning": [
    {{
      "criterion": "description of criterion",
      "met": true/false,
      "citizen_value": "citizen's relevant value",
      "required_value": "what the scheme requires",
      "citation": {{
        "text": "EXACT quote from the document (under 20 words)",
        "page": page_number,
        "section": "section name",
        "pdf_name": "PDF filename"
      }}
    }}
  ],
  "benefits": "what the citizen will receive",
  "checklist": [
    {{
      "step": 1,
      "title": "step title",
      "description": "detailed instruction",
      "documents_needed": ["doc1", "doc2"],
      "office": "office name if applicable",
      "online_url": "URL if applicable",
      "deadline": "deadline if applicable"
    }}
  ],
  "application_url": "official URL or null",
  "deadline": "deadline or No fixed deadline"
}}

CRITICAL RULES:
- Never mark eligible=true without citing the exact clause.
- If you cannot find evidence for a criterion, note it as uncertain.
- Citations must quote EXACT text from the provided documents.
- Checklist must be actionable step-by-step instructions.
"""
        
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            text = re.sub(r'```json|```', '', text).strip()
            match_data = json.loads(text)
            matches.append(match_data)
        except Exception as e:
            print(f"Error processing scheme {scheme_id}: {e}")
            continue
    
    return matches
