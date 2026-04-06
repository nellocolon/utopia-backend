# UTOPIA Backend — Context per Claude Code

## Cos'è UTOPIA
Piattaforma SaaS gamificata per community di token su Solana.
Creator lanciano community gamificate su X (Twitter), utenti completano missioni, guadagnano XP,
salgono in classifica e vincono premi in SOL/$UTOPIA finanziati dalle creator fees di pump.fun/bags.fm.

## Stack
- **Backend**: FastAPI Python 3.12 + asyncpg + Supabase
- **Database**: PostgreSQL 15 su Supabase (30 tabelle, schema in utopia_schema.sql)
- **Deploy**: Railway (Dockerfile pronto)
- **Frontend**: React + Vite (file separato: utopia_site_v8.jsx)
- **Blockchain**: Solana Mainnet + Anchor

## Struttura cartella
```
utopia-backend/
├── app/
│   ├── main.py              # FastAPI app factory
│   ├── config.py            # Settings pydantic-settings
│   ├── database.py          # Supabase client + asyncpg pool
│   ├── middleware/auth.py   # JWT auth dependency
│   ├── schemas/__init__.py  # Tutti i Pydantic schemas
│   ├── services/
│   │   ├── x_api.py         # SocialData.tools wrapper
│   │   └── verification.py  # Orchestratore verifica missioni
│   └── routers/
│       ├── auth.py          # X OAuth 2.0 PKCE
│       ├── communities.py   # Explore, create, join, leaderboard
│       ├── missions.py      # Lista, submit, history
│       ├── competitions.py  # Lista, enter, classifica
│       ├── user.py          # Dashboard, streak, stake
│       ├── offerwall.py     # Postback Offertoro/AdGate/Freecash
│       └── fee_routing.py   # pump.fun webhook
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env                     # NON committare su GitHub
```

## Variabili d'ambiente necessarie
Tutte nel file .env — vedere .env.example per la lista completa.
Chiavi principali: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL

## Prossimi step da completare
1. Compilare .env con le chiavi Supabase reali
2. Verificare che lo schema SQL sia applicato su Supabase (utopia_schema.sql)
3. `pip install -r requirements.txt`
4. `uvicorn app.main:app --reload` per avviare in locale
5. Testare /health → deve rispondere {"status":"ok"}
6. Creare repo GitHub e fare push
7. Collegare Railway alla repo e fare deploy
8. Configurare variabili d'ambiente su Railway
9. Registrare app su developer.twitter.com per X OAuth
10. Collegare il frontend React (utopia_site_v8.jsx) agli endpoint reali

## Note importanti
- Il .env NON va mai committato su GitHub (già in .gitignore)
- La DATABASE_URL usa il prefisso postgresql+asyncpg:// (non postgresql://)
- Lo schema SQL va applicato una volta sola tramite Supabase SQL Editor
- Le funzioni PL/pgSQL (award_xp, claim_streak) sono già nel database schema
- RLS è abilitata su tutte le tabelle — il backend usa service_role_key per bypassarla dove necessario

## Endpoint disponibili
- GET  /health
- GET  /auth/x/init
- GET  /auth/x/callback
- POST /auth/wallet
- GET  /auth/me
- GET  /communities
- GET  /communities/{slug}
- POST /communities
- POST /communities/{id}/join
- GET  /communities/{id}/leaderboard
- GET  /missions/{community_id}
- POST /missions/submit
- GET  /missions/completions/{community_id}
- GET  /competitions/{community_id}
- POST /competitions/{id}/enter
- GET  /competitions/{id}/leaderboard
- GET  /me/dashboard/{community_id}
- POST /me/streak/{community_id}
- POST /me/stake/{community_id}
- DELETE /me/stake/{community_id}
- POST /offerwall/postback/offertoro
- POST /offerwall/postback/adgate
- POST /offerwall/postback/freecash
- GET  /fee-routing/{id}/setup
- POST /fee-routing/{id}/confirm
- POST /fee-routing/webhook
