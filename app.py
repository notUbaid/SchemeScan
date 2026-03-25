import os
import re
from io import BytesIO
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, redirect, send_file
from flask_cors import CORS
from config import Config
from blueprints.auth import auth_bp
from blueprints.query import query_bp
from blueprints.schemes import schemes_bp
from blueprints.documents import documents_bp
from blueprints.forms import forms_bp
from blueprints.translate import translate_bp
from blueprints.admin import admin_bp

# Import compatibility tools
from utils.language_utils import translate_to_english
from rag.generator import extract_citizen_profile
from rag.retriever import retrieve_relevant_chunks
from utils.ollama_client import get_ollama_response
from utils.firebase_client import db, query_collection
import json


def _resolve_scheme_doc(scheme_id: str | None, title: str | None):
    """Return (firestore_document_id, data_dict) or (None, None)."""
    sid = (scheme_id or "").strip()
    ttl = (title or "").strip()
    if sid:
        doc = db.collection("schemes").document(sid).get()
        if doc.exists:
            return doc.id, doc.to_dict()
        try:
            q = db.collection("schemes").where("scheme_id", "==", sid).limit(1)
            for d in q.stream():
                return d.id, d.to_dict()
        except Exception:
            pass
    if ttl:
        tnorm = ttl.lower().strip()
        best_pair = None
        best_len = 99999
        for doc in db.collection("schemes").stream():
            s = doc.to_dict()
            n = (s.get("name") or "").strip().lower()
            if not n:
                continue
            if n == tnorm:
                return doc.id, s
            if tnorm in n or n in tnorm:
                ln = len(n)
                if ln < best_len:
                    best_len = ln
                    best_pair = (doc.id, s)
        if best_pair:
            return best_pair
    return None, None


def _safe_pdf_filename(name: str) -> str:
    base = re.sub(r"[^\w\s\-.]", "", (name or "scheme").strip())[:72]
    base = re.sub(r"\s+", "_", base).strip("._-") or "scheme"
    return f"{base}.pdf"


def create_app():
    app = Flask(__name__, static_folder='static')
    app.config.from_object(Config)
    
    CORS(app, resources={
        r"/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })
    
    # Existing API Blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(query_bp, url_prefix="/api/v1/query")
    app.register_blueprint(schemes_bp, url_prefix="/api/v1/schemes")
    app.register_blueprint(documents_bp, url_prefix="/api/v1/documents")
    app.register_blueprint(forms_bp, url_prefix="/api/v1/forms")
    app.register_blueprint(translate_bp, url_prefix="/api/v1/translate")
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
    
    # JSON health for frontend (GET / serves index.html, so never use / for status checks)
    @app.route('/api/health')
    def api_health():
        schemes_count = 0
        try:
            schemes_ref = db.collection('schemes').count().get()
            schemes_count = schemes_ref[0][0].value if schemes_ref else 0
        except Exception:
            pass
        return jsonify({
            "status": "online",
            "rag_ready": True,
            "rag_chunks": 0,
            "schemes_in_db": schemes_count,
        })

    @app.route('/login')
    def serve_login():
        return send_from_directory(app.static_folder, 'login.html')

    # Compatibility Layer for index.html
    @app.route('/')
    def serve_index():
        try:
            # Check if index.html is in static folder
            if os.path.exists(os.path.join(app.static_folder, 'index.html')):
                return send_from_directory(app.static_folder, 'index.html')
            
            # Backend Status Check (if index.html isn't there yet)
            schemes_count = 0
            try:
                schemes_ref = db.collection('schemes').count().get()
                schemes_count = schemes_ref[0][0].value if schemes_ref else 0
            except: pass
            
            return jsonify({
                "status": "online",
                "rag_ready": True,
                "schemes_in_db": schemes_count,
                "note": "Frontend index.html not found in static/. Integrated project will serve it here."
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/user/parse-nl', methods=['POST'])
    def parse_profile():
        data = request.get_json()
        text = data.get('text', '')
        lang = data.get('language', 'en')
        
        try:
            english_text = translate_to_english(text, lang)
            profile = extract_citizen_profile(english_text)
            return jsonify({"parsed": profile})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/rag/query', methods=['POST'])
    def rag_query():
        data = request.get_json()
        question = data.get('question', '')
        profile = data.get('profile', {})
        history = data.get('history', [])
        language = data.get('language', 'en')
        
        try:
            # Better retrieval using profile context if available
            chunks = retrieve_relevant_chunks(profile, state=data.get('state_filter', ''))
            context = "\n\n".join([f"Source: {c['metadata'].get('scheme_name', 'Document')}\n{c['text']}" for c in chunks[:7]])
            
            system_prompt = (
                "You are 'SchemeScan' (also known as 'Sarkari Saathi'), a highly specialized AI assistant for Indian government schemes. "
                "Your objective is to provide precise, helpful, and empathetic assistance to Indian citizens seeking government benefits. "
                "\n\nIDENTITY RULES:"
                "\n- NEVER change your name. You are always SchemeScan."
                "\n- Always maintain a professional, trustworthy, and supportive persona."
                "\n- You can handle general conversation, but you must always guide the user towards relevant schemes if appropriate."
                "\n\nCONTEXTUAL KNOWLEDGE:"
                f"\nUser Profile: {json.dumps(profile)}"
                "\n\nUse the following verified document snippets to answer the user's question accurately. "
                "If the answer is not in the context, use your general knowledge but clearly state if it's not from the official documents provided. "
                "Prioritize accuracy for eligibility, benefits, and required documents."
                "\n\nContext:\n" + context
            )
            
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add limited history for conversational context
            for msg in history[-5:]:
                if 'role' in msg and 'content' in msg:
                    messages.append({"role": msg['role'], "content": msg['content']})
            
            # Current question
            messages.append({"role": "user", "content": question})
            
            answer = get_ollama_response(messages)
            
            sources = []
            seen_files = set()
            for c in chunks[:5]:
                fname = c['metadata'].get('scheme_name', 'Source')
                if fname not in seen_files:
                    sources.append({
                        "file": fname,
                        "scheme_id": c['metadata'].get('scheme_id'),
                    })
                    seen_files.add(fname)
                
            return jsonify({
                "answer": answer,
                "sources": sources,
                "contradictions": [],
                "completeness": 5 # Higher means more info extracted
            })
        except Exception as e:
            print(f"RAG Query Error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/match', methods=['POST'])
    def match_schemes_compat():
        data = request.get_json()
        # Simplified matching for index.html
        try:
            # Fetch all and filter client-side or simple server-side
            schemes_ref = db.collection('schemes').limit(10).stream()
            results = []
            for doc in schemes_ref:
                s = doc.to_dict()
                sid = s.get("scheme_id") or doc.id
                results.append({
                    "title": s.get('name', 'Unknown Scheme'),
                    "benefit_text": (s.get('benefits') or s.get('benefit_text') or ''),
                    "category": s.get('category', 'All'),
                    "state": s.get('state', 'Central'),
                    "eligibility": s.get('eligibility', ''),
                    "scheme_id": sid,
                })
            return jsonify({"schemes": results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/search', methods=['GET'])
    def search_schemes():
        q = request.args.get('q', '').lower()
        try:
            # Simple keyword match
            schemes_ref = db.collection('schemes').stream()
            results = []
            for doc in schemes_ref:
                s = doc.to_dict()
                if q in s.get('name', '').lower() or q in s.get('category', '').lower():
                    sid = s.get("scheme_id") or doc.id
                    results.append({
                        "title": s.get('name', 'Unknown Scheme'),
                        "benefit_text": (s.get('benefits') or s.get('benefit_text') or ''),
                        "category": s.get('category', 'All'),
                        "state": s.get('state', 'Central'),
                        "eligibility": s.get('eligibility', ''),
                        "scheme_id": sid,
                    })
                    if len(results) >= 5: break
            return jsonify({"schemes": results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/user/save-doc', methods=['POST'])
    def save_doc_compat():
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        import tempfile
        import os
        from ocr.aadhaar_parser import parse_aadhaar
        # Try to parse as Aadhaar as default or just dummy for compat
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name
                
            # For now, let's just return a placeholder profile if it's a demo
            # In a real scenario, we'd use the parsers
            # extracted = parse_aadhaar(tmp_path)
            
            # Simulated OCR Response matching UI expectations
            return jsonify({
                "doc_type": "aadhaar",
                "ocr_extracted": {
                    "name": "Arjun Kumar",
                    "age": 42,
                    "gender": "Male",
                    "state": "Gujarat"
                },
                "merged_profile": {
                    "name": "Arjun Kumar",
                    "age": 42,
                    "gender": "Male",
                    "state": "Gujarat",
                    "occupation": "Farmer"
                }
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            if 'tmp_path' in locals(): os.unlink(tmp_path)

    @app.route("/scheme/pdf", methods=["GET"])
    def scheme_pdf_download():
        scheme_id = request.args.get("scheme_id", default="", type=str).strip()
        title = request.args.get("title", default="", type=str).strip()
        if not scheme_id and not title:
            return jsonify({"error": "Provide scheme_id or title"}), 400

        doc_id, data = _resolve_scheme_doc(scheme_id or None, title or None)
        if not data:
            return jsonify({"error": "Scheme not found"}), 404

        display_name = data.get("name") or title or doc_id or "scheme"
        download_name = _safe_pdf_filename(display_name)

        pdf_path = data.get("pdf_path")
        if pdf_path:
            try:
                corpus_root = Path(Config.PDF_CORPUS_PATH)
                if not corpus_root.is_absolute():
                    corpus_root = (Path(app.root_path) / corpus_root).resolve()
                else:
                    corpus_root = corpus_root.resolve()
                candidate = Path(pdf_path)
                if not candidate.is_absolute():
                    candidate = (corpus_root / pdf_path).resolve()
                else:
                    candidate = candidate.resolve()
                candidate.relative_to(corpus_root)
                if candidate.is_file():
                    return send_file(candidate, as_attachment=True, download_name=download_name)
            except (ValueError, OSError):
                pass
            try:
                app_root = Path(app.root_path).resolve()
                rel = (app_root / pdf_path).resolve()
                rel.relative_to(app_root)
                if rel.is_file():
                    return send_file(rel, as_attachment=True, download_name=download_name)
            except (ValueError, OSError):
                pass

        pdf_url = data.get("official_pdf_url") or data.get("pdf_url")
        if pdf_url and str(pdf_url).strip().lower().startswith(("http://", "https://")):
            return redirect(str(pdf_url).strip(), code=302)

        from utils.scheme_pdf import build_scheme_summary_pdf_bytes

        merged = {**data, "scheme_id": data.get("scheme_id") or doc_id}
        blob = build_scheme_summary_pdf_bytes(merged)
        return send_file(
            BytesIO(blob),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=8000) # Pointing to 8000 as per index.html config
