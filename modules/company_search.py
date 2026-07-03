"""
Semantic company search (RAG) — retrieve the most relevant companies from the
curated Israeli company directory using embeddings + cosine similarity, then
ask Claude to explain the matches grounded in the retrieved facts only.

Retrieval: sentence-transformers embeddings (falls back to TF-IDF if the
embedding model can't be loaded, e.g. on a memory-constrained host).
Generation: a single Claude call that may only reference the retrieved
companies — it is not allowed to invent companies that weren't retrieved.
"""
import json
import numpy as np
import streamlit as st

from config import ISRAELI_COMPANIES
from modules.career_advisor import _claude

_EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _company_text(co: dict, sector: str) -> str:
    parts = [co.get("name", ""), sector, co.get("location", "")]
    if co.get("he"):
        parts.append(co["he"])
    for tag in ("specialties",):
        if co.get(tag):
            parts.append(", ".join(co[tag]))
    return " — ".join(p for p in parts if p)


def _corpus() -> list[dict]:
    rows = []
    for sector, companies in ISRAELI_COMPANIES.items():
        for co in companies:
            rows.append({**co, "sector": sector, "text": _company_text(co, sector)})
    return rows


@st.cache_resource(show_spinner="Loading semantic search index...")
def _dense_index():
    """Returns (encoder, embeddings) using sentence-transformers, or None if unavailable."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(_EMBED_MODEL_NAME)
        rows = _corpus()
        texts = [r["text"] for r in rows]
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return model, np.asarray(embeddings), rows
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _sparse_index():
    """TF-IDF fallback — always available, no model download required."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    rows = _corpus()
    texts = [r["text"] for r in rows]
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(texts)
    return vectorizer, matrix, rows


def semantic_search(query: str, top_k: int = 8) -> list[dict]:
    """Retrieval step: returns the top_k most relevant companies for a free-text query."""
    if not query.strip():
        return []

    dense = _dense_index()
    if dense is not None:
        model, embeddings, rows = dense
        q_vec = model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        scores = embeddings @ q_vec
        method = "embeddings"
    else:
        from sklearn.metrics.pairwise import cosine_similarity
        vectorizer, matrix, rows = _sparse_index()
        q_vec = vectorizer.transform([query])
        scores = cosine_similarity(q_vec, matrix)[0]
        method = "tfidf"

    order = np.argsort(scores)[::-1][:top_k]
    return [{**rows[i], "score": float(scores[i]), "retrieval_method": method} for i in order]


def explain_matches(query: str, profile: dict, matches: list[dict]) -> str:
    """Generation step: Claude explains the retrieved matches — grounded, not free recall."""
    if not matches:
        return ""

    facts = "\n".join(
        f"- {m['name']} ({m['sector']}, {m.get('location', 'N/A')})"
        for m in matches
    )
    skills = ", ".join(profile.get("skills", [])[:10]) or "not specified"
    target_roles = ", ".join(profile.get("target_roles", [])) or "not specified"

    prompt = f"""A candidate searched for: "{query}"

Candidate profile — skills: {skills}; target roles: {target_roles}

Below is the ONLY set of companies retrieved as relevant matches. You must not mention or invent any company that is not in this list.

RETRIEVED COMPANIES:
{facts}

For each company, write one short sentence on why it could be a fit for this search and profile, based only on the sector/location shown above and the candidate's stated skills/roles. Keep the whole answer under 150 words."""

    return _claude(prompt, max_tokens=500, fast=True)
