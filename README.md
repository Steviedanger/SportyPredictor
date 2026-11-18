# ⚽ SportyPredictor

An automated, API-driven full-stack football match prediction platform. It fetches live fixtures from official sports APIs, runs a custom Poisson distribution and Elo rating model, and serves original match probabilities and exact scorelines.

## 🚀 Tech Stack
- **Backend & Prediction Engine:** Python (`requests`, `supabase`, `python-dotenv`)
- **Database & Hosting:** Supabase (PostgreSQL)
- **Automation:** GitHub Actions (Cron Jobs running twice daily)
- **Frontend:** Next.js + Tailwind CSS (Hosted on Vercel)
- **Data Source:** Football-Data.org API

---

## 🛠️ Project Roadmap & Status
- [x] **Phase 1:** Database schema design & API data ingestion (`scraper.py`)
- [x] **Phase 2:** Poisson + Elo mathematical prediction engine (`predictor.py`)
- [x] **Phase 3:** Automated daily pipeline & GitHub Actions workflow (`main.py`)
- [ ] **Phase 4:** Next.js frontend web app dashboard & value pick filtering
- [ ] **Phase 5:** Polish, SEO optimization, and monetization placeholders

---

## ⚙️ Local Setup & Running

1. Clone the repository and navigate into the folder:
   ```bash
   git clone [https://github.com/Steviedanger/SportyPredictor.git](https://github.com/Steviedanger/SportyPredictor.git)
   cd SportyPredictor