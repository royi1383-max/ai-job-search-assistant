# AI Job Search Assistant

A Streamlit app that helps you run a structured job search: search jobs from multiple sources, tailor a CV and cover letter per role with Claude, track applications through a kanban-style pipeline, and get AI-assisted career advice — with a built-in, curated directory of Israeli companies by sector.

## Features

- **Job search** — pulls listings from multiple sources (Adzuna, Greenhouse, Lever) with keyword/role/experience-level filtering
- **Company directory** — curated list of Israeli companies grouped by sector (VC, fintech, tech startups, consulting, banks, pharma, industrial, and more), each with career-page links
- **CV builder** — generates a tailored CV per job using your profile + the job description (Claude API)
- **Cover letter generator** — same idea, for cover letters
- **Application tracker** — kanban board (Saved → Applied → Phone Screen → Interview → Offer / Rejected)
- **Career advisor** — AI-assisted guidance on career paths and positioning
- **Smart company match (RAG)** — free-text semantic search over the company directory (retrieval), then Claude explains the matches grounded only in what was retrieved (generation). Uses TF-IDF by default; install `sentence-transformers` for dense embedding-based retrieval instead (see below)
- **Bilingual UI** — Hebrew and English

## Tech stack

Python, Streamlit, Anthropic (Claude) API, pandas, scikit-learn, BeautifulSoup, PyMuPDF, python-docx, reportlab

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your own API keys
streamlit run app.py
```

Then fill in your profile from the app's Profile tab — see `data/profile.example.json` for the expected fields. Drop your resume file(s) into a `resumes/` folder in the project root if you want the CV builder to reference an existing resume.

**Optional — dense embeddings for Smart Company Match:** by default the semantic search uses TF-IDF (no extra download, works everywhere, including free-tier hosting). For higher-quality dense embedding retrieval, install `sentence-transformers` locally:
```bash
pip install sentence-transformers
```
The app detects it automatically and switches to embeddings — no code changes needed. It's left out of `requirements.txt` by default since it pulls in `torch`, which is too heavy for constrained free-tier deployments.

## Project structure

- `app.py` — main Streamlit app / navigation
- `modules/` — one module per feature (job search, CV builder, cover letters, tracker, career advisor, profile, company database, semantic company search)
- `config.py` — role categories, the Israeli company directory, and app configuration
- `data/profile.example.json` — template for your personal profile (copy or recreate as `data/profile.json`, which is gitignored)
