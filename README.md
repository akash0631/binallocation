# RDC Allocation & Route Optimizer

Web-based 4-phase warehouse allocation engine: Bin Selection → Route Optimization → Picker Splitting → KPI Generation.

## Deploy on Streamlit Cloud (Free)

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub → select this repo → main branch → `app.py`
4. Deploy

Your team gets a URL like `https://your-app.streamlit.app`

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`

## How It Works

| Phase | What It Does |
|-------|-------------|
| 1. Bin Selection | Matches demand to bins using scoring (exact match, consolidation, empties). Global ledger depletion across stores. |
| 2. Route Optimization | Parses bin codes, computes serpentine walk keys, assigns pick sequences per store-floor. |
| 3. Picker Splitting | Splits heavy floors into multiple pickers. Each picker stays on ONE floor. Unique numbering per store. |
| 4. KPIs | Fulfillment %, shortages, bins visited, pickers required, split rate per store. |

## Files

```
├── app.py              ← Streamlit UI
├── optimizer.py        ← Engine (4-phase logic)
├── requirements.txt    ← Dependencies
└── .streamlit/
    └── config.toml     ← Theme config
```
