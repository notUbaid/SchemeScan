import pdfplumber
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer
from config import Config
import os, json, re, uuid
from pathlib import Path

chroma_client = chromadb.PersistentClient(path=Config.CHROMA_DB_PATH)
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

sentence_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

collection = chroma_client.get_or_create_collection(
    name="government_schemes",
    embedding_function=sentence_ef,
    metadata={"hnsw:space": "cosine"}
)

SECTION_HEADERS = [
    'eligibility', 'patra', 'benefits', 'labh', 'documents required',
    'application process', 'how to apply', 'objective', 'scope',
    'financial assistance', 'amount', 'deadline', 'last date',
    'income limit', 'age limit', 'category'
]

def extract_chunks_from_pdf(pdf_path: str, metadata: dict) -> list[dict]:
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        current_section = "General"
        current_text = ""
        
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            lines = text.split('\n')
            
            for line in lines:
                line_lower = line.lower().strip()
                
                # Detect section headers
                is_header = any(h in line_lower for h in SECTION_HEADERS)
                if is_header and len(line.strip()) < 80:
                    if current_text.strip():
                        chunks.append({
                            'id': str(uuid.uuid4()),
                            'text': current_text.strip(),
                            'section': current_section,
                            'page': page_num,
                            **metadata
                        })
                        current_text = ""
                    current_section = line.strip()
                else:
                    current_text += " " + line
                
                # Chunk if text gets long
                if len(current_text.split()) > 200:
                    chunks.append({
                        'id': str(uuid.uuid4()),
                        'text': current_text.strip(),
                        'section': current_section,
                        'page': page_num,
                        **metadata
                    })
                    current_text = ""
        
        if current_text.strip():
            chunks.append({
                'id': str(uuid.uuid4()),
                'text': current_text.strip(),
                'section': current_section,
                'page': len(pdf.pages),
                **metadata
            })
    
    return chunks

def ingest_pdf(pdf_path: str, scheme_name: str, level: str, 
               state: str = "", category: str = "", scheme_id: str = None):
    if not scheme_id:
        scheme_id = str(uuid.uuid4())
    
    metadata = {
        'scheme_id': scheme_id,
        'scheme_name': scheme_name,
        'level': level,          # 'central' or 'state'
        'state': state,          # empty string for central schemes
        'category': category,
        'pdf_path': pdf_path
    }
    
    chunks = extract_chunks_from_pdf(pdf_path, metadata)
    
    if not chunks:
        return {'scheme_id': scheme_id, 'chunks': 0}
    
    collection.upsert(
        ids=[c['id'] for c in chunks],
        documents=[c['text'] for c in chunks],
        metadatas=[{k: v for k, v in c.items() if k != 'text' and k != 'id'} 
                   for c in chunks]
    )
    
    # Update Firestore scheme record
    from utils.firebase_client import create_document
    scheme_data = {
        'name': scheme_name,
        'level': level,
        'state': state,
        'category': category,
        'pdf_path': pdf_path,
        'chunk_count': len(chunks),
        'active': True
    }
    create_document('schemes', scheme_data, scheme_id)
    
    return {'scheme_id': scheme_id, 'chunks': len(chunks)}

def ingest_all_pdfs():
    """Run once to ingest all PDFs in scheme_pdfs/ directory"""
    corpus_path = Path(Config.PDF_CORPUS_PATH)
    results = []
    
    for level_dir in ['central', 'state']:
        level_path = corpus_path / level_dir
        if level_path.exists():
            for state_or_pdf in level_path.iterdir():
                if state_or_pdf.is_dir():  # state subdirectory
                    state_name = state_or_pdf.name
                    for pdf_file in state_or_pdf.glob('*.pdf'):
                        result = ingest_pdf(
                            str(pdf_file),
                            scheme_name=pdf_file.stem.replace('_', ' ').title(),
                            level='state',
                            state=state_name,
                            category=detect_category_from_name(pdf_file.stem)
                        )
                        results.append(result)
                elif state_or_pdf.suffix == '.pdf':  # central PDF
                    result = ingest_pdf(
                        str(state_or_pdf),
                        scheme_name=state_or_pdf.stem.replace('_', ' ').title(),
                        level='central',
                        category=detect_category_from_name(state_or_pdf.stem)
                    )
                    results.append(result)
    
    return results

def detect_category_from_name(filename: str) -> str:
    f = filename.lower()
    if any(k in f for k in ['farm', 'kisan', 'crop', 'agri', 'soil']):
        return 'agriculture'
    if any(k in f for k in ['scholar', 'education', 'student', 'school']):
        return 'education'
    if any(k in f for k in ['health', 'ayush', 'janani', 'matru']):
        return 'health'
    if any(k in f for k in ['housing', 'awas', 'home', 'pucca']):
        return 'housing'
    if any(k in f for k in ['employ', 'mudra', 'skill', 'rozgar']):
        return 'employment'
    return 'general'
