# Mesa Careers AI

AI-powered career matching pipeline for Mesa School of Business.

Input: LinkedIn URL or PDF export  
Output: Ranked job leads + personalised outreach drafts + internal dossier  
Time: ~3–5 minutes per student profile

## Quick Start

### 1. Clone & install
```bash
git clone <repo>
cd mesa-careers-ai
pip install -r requirements.txt
```

### 2. Set up environment
```bash
cp .env.example .env
# Fill in all API keys in .env
```

### 3. Set up Supabase
```
# Create a project at https://supabase.com
# Run db/schema.sql in the Supabase SQL editor
```

### 4. Run the dashboard
```bash
streamlit run app.py
```

### 5. Docker (alternative)
```bash
docker-compose up
```

## API Keys Needed

| Service     | Purpose                  | Cost         | Link                          |
|-------------|--------------------------|--------------|-------------------------------|
| Anthropic   | LLM for all 6 stages     | ~$0.05/run   | console.anthropic.com         |
| Proxycurl   | LinkedIn URL scraping    | $0.01/call   | nubela.co/proxycurl           |
| Apify       | LinkedIn Jobs scraping   | ~$0.10/run   | apify.com                     |
| Tavily      | Web + post search        | Free tier    | tavily.com                    |
| Twitter API | Hiring post signals      | Free tier    | developer.twitter.com         |
| Apollo.io   | Email enrichment         | Free tier    | apollo.io                     |
| Hunter.io   | Email fallback           | Free tier    | hunter.io                     |
| Supabase    | Database                 | Free tier    | supabase.com                  |

## Pipeline Stages

| Stage | Module           | What it does                              |
|-------|------------------|-------------------------------------------|
| S1    | s1_ingest.py     | Scrape LinkedIn URL or parse PDF          |
| S2    | s2_synthesise.py | Generate candidate model via Claude       |
| S3    | s3_discover.py   | Find formal listings + hidden signals     |
| S4    | s4_rank.py       | Score + rank all leads (weighted sum)     |
| S5    | s5_outreach.py   | Generate personalised outreach on click   |
| S6    | s6_dossier.py    | Generate 1-page internal dossier         |
