from rag.ingestor import collection

def retrieve_relevant_chunks(profile: dict, category: str = 'all',
                              state: str = '', top_k: int = 20) -> list[dict]:
    
    # Build search query from profile
    query_parts = []
    if profile.get('occupation'):
        query_parts.append(f"{profile['occupation']} scheme eligibility")
    if profile.get('annual_income'):
        query_parts.append(f"income limit {profile['annual_income']}")
    if profile.get('age'):
        query_parts.append(f"age {profile['age']} years")
    if profile.get('category'):
        query_parts.append(f"{profile['category']} category")
    if profile.get('land_holding'):
        query_parts.append(f"land {profile['land_holding']} acres")
    if profile.get('gender'):
        query_parts.append(profile['gender'])
    
    query_parts.append("eligibility criteria documents required benefits")
    query_text = " ".join(query_parts)
    
    # Build where filter
    where_filter = {}
    if category and category != 'all':
        where_filter['category'] = category
    
    # Search for central + state-specific chunks
    results = collection.query(
        query_texts=[query_text],
        n_results=top_k,
        where=where_filter if where_filter else None,
        include=['documents', 'metadatas', 'distances']
    )
    
    chunks = []
    if results['documents'] and results['documents'][0]:
        for doc, meta, dist in zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0]
        ):
            chunks.append({
                'text': doc,
                'metadata': meta,
                'relevance_score': 1 - dist
            })
    
    # Also search specifically for the user's state schemes
    if state and state.lower() not in [c['metadata'].get('state','').lower() 
                                        for c in chunks]:
        state_results = collection.query(
            query_texts=[query_text],
            n_results=10,
            where={'state': state},
            include=['documents', 'metadatas', 'distances']
        )
        if state_results['documents'] and state_results['documents'][0]:
            for doc, meta, dist in zip(
                state_results['documents'][0],
                state_results['metadatas'][0],
                state_results['distances'][0]
            ):
                chunks.append({
                    'text': doc,
                    'metadata': meta,
                    'relevance_score': 1 - dist
                })
    
    return chunks
