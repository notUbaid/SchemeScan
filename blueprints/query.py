from flask import Blueprint, request, jsonify, g
from utils.auth_middleware import require_auth
from utils.firebase_client import create_document, update_document, db
from utils.language_utils import detect_language, translate_to_english
from utils.audio_utils import transcribe_audio
from analytics.aggregator import log_query_event
import json, uuid

query_bp = Blueprint('query', __name__)

@query_bp.route('/transcribe', methods=['POST'])
@require_auth
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    transcribed_text, detected_lang = transcribe_audio(audio_file)
    
    return jsonify({
        'transcribed_text': transcribed_text,
        'detected_language': detected_lang
    })

@query_bp.route('/process', methods=['POST'])
@require_auth
def process_query():
    data = request.get_json()
    raw_input = data.get('raw_input', '').strip()
    state = data.get('state', '')
    category = data.get('category', 'all')
    preferred_language = data.get('preferred_language', 'en')
    
    if not raw_input:
        return jsonify({'error': 'Query text is required'}), 400
    
    detected_language = detect_language(raw_input)
    english_text = translate_to_english(raw_input, detected_language)
    
    # Extract structured profile using LLM
    from rag.generator import extract_citizen_profile
    extracted_profile = extract_citizen_profile(english_text, state)
    
    query_id = str(uuid.uuid4())
    query_data = {
        'user_uid': g.user_uid,
        'raw_input': raw_input,
        'detected_language': detected_language,
        'preferred_language': preferred_language,
        'translated_input': english_text,
        'extracted_profile': extracted_profile,
        'state_filter': state or extracted_profile.get('state', ''),
        'category': category,
        'status': 'processed',
        'scheme_match_count': 0
    }
    
    create_document('queries', query_data, query_id)
    log_query_event(g.user_uid, query_id, detected_language, category, state)
    
    return jsonify({
        'query_id': query_id,
        'extracted_profile': extracted_profile,
        'detected_language': detected_language
    })

@query_bp.route('/chat', methods=['POST'])
@require_auth
def chat_with_ollama():
    data = request.get_json()
    user_message = data.get('message', '').strip()
    profile = data.get('profile', {})
    schemes = data.get('schemes', [])
    chat_history = data.get('history', []) # Previous messages in the conversation
    
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400
        
    system_prompt = (
        "You are Sarkari Saathi, an expert AI assistant for Indian government schemes. "
        "Your ONLY job is to write a warm, 1-2 sentence introductory message acknowledging the user's profile and stating that you found schemes for them below. "
        "DO NOT list the schemes. DO NOT explain the reasoning. DO NOT mention the benefits in the text. "
        "The schemes and details will be displayed as interactive cards below your message by the UI. "
        "Just say something like: 'Based on your profile, here are the exact and closest schemes I found for you:'\n\n"
    )
    
    context_info = f"User Profile: {json.dumps(profile)}\n\nMatched Schemes for the user:\n"
    if schemes:
        for idx, s in enumerate(schemes[:5]): # limit to top 5 to avoid context overflow
            match_type = s.get('match_type', 'Unknown')
            context_info += f"{idx+1}. {s.get('scheme_name', 'Unknown')} [{match_type}]\n"
            context_info += f"   Reasoning: {' '.join(s.get('reasoning', []))}\n"
            context_info += f"   Benefits: {s.get('benefits', '')}\n"
    else:
        context_info += "None found yet. Ask the user for more details like age, income, state, or category.\n"
        
    system_prompt += context_info
    
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Add mapped history (limit to last few turns to manage context)
    for msg in chat_history[-6:]:
        if msg['role'] in ['user', 'assistant']:
            messages.append({"role": msg['role'], "content": msg['content']})
            
    # Add current user message
    messages.append({"role": "user", "content": user_message})
    
    from utils.ollama_client import get_ollama_response
    # We default to llama3, but if it fails it fails.
    bot_reply = get_ollama_response(messages)
    
    return jsonify({
        'reply': bot_reply
    })
