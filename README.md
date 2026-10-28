# Copilot AI : copilote financier quantitatif

Application web de suivi et d'analyse de portefeuille boursier (investisseur en euros) :
données de marché scrapées et historisées, performance réelle, métriques de risque,
simulation Monte Carlo, stress tests historiques,
et un assistant RAG qui répond à partir des fiches d'ETF collectées, en signalant tout chiffre
qu'il n'a pas trouvé dans ses sources.

> Projet personnel de démonstration : les résultats sont indicatifs et ne constituent pas un conseil en investissement.

## Fonctionnalités

| Domaine | Ce que fait l'application |
|---|---|
| Portefeuille | Journal d'achats/ventes enregistré en base ; prix d'achat par défaut = cours de clôture réel du jour (converti en EUR) ; ventes impossibles au-delà de la quantité détenue à leur date |
| Performance réelle | TWR (pondéré dans le temps, norme GIPS), XIRR (pondéré par les capitaux), volatilité, Sharpe, Sortino et drawdown réalisés, comparaison à un benchmark recevant les mêmes versements |
| Risque ex-ante | Volatilité, Sharpe, Sortino, drawdown de la composition actuelle sur 1 à 10 ans d'historique |
| Monte Carlo | Mouvement brownien géométrique multi-actifs corrélé (Cholesky), 10 000 trajectoires, VaR et Expected Shortfall 95 % exprimées en perte, graine optionnelle, stratégie DCA en euros |
| Stress tests | Pires épisodes réellement observés (pire baisse, pire mois du portefeuille, pire mois du marché) rejoués sur les positions actuelles, bêtas historiques |
| Look-through et géographie | Principales positions, répartition par pays et par secteur des ETF (Yahoo Finance, JustETF) |
| Assistant RAG | Recherche sémantique (pgvector, similarité cosinus) sur les fiches d'ETF, réponse LLM (Groq), vérification automatique des chiffres cités |

## Architecture

```
            ┌──────────────┐   /api, /ws (proxy Vite)   ┌─────────────────────┐
 Navigateur │ React + Vite │ ─────────────────────────► │  FastAPI (uvicorn)  │
            └──────────────┘ ◄──── WebSocket ────────── │  app/api/main.py    │
                                                         └──┬────────┬────────┘
                                         tâches Monte Carlo │        │ SQL (asyncpg)
                                     ┌──────────────────────▼─┐   ┌──▼──────────────────────┐
                                     │ Celery worker + Redis  │   │ PostgreSQL 16 + pgvector│
                                     └────────────────────────┘   └──▲──────────────────────┘
                                                                     │ pipelines (HTTP)
                                     ┌───────────────────────────────┴──┐
                                     │ Airflow : cours (quotidien),       │
                                     │ fiches ETF + RAG (hebdomadaire)    │
                                     └────────────────────────────────────┘
```

| Couche | Technologies |
|---|---|
| Backend | Python 3.12, FastAPI, asyncpg, Celery, Redis, NumPy, Polars |
| Données | yfinance, JustETF (BeautifulSoup), PostgreSQL, pgvector |
| RAG | sentence-transformers (all-MiniLM-L6-v2), LangChain, Groq |
| Front | React 19, Vite, Recharts |
| Outillage | uv, pytest, Ruff, Docker Compose, Airflow |

## Données : sources et stockage

Aucune donnée de marché n'est saisie à la main : tout est collecté puis enregistré en base, et
une information non publiée par les sources reste vide (aucune valeur de repli inventée).

| Table | Contenu | Source | Mise à jour |
|---|---|---|---|
| `assets` | Référentiel (ticker, nom, classe, devise de cotation) | Wikipédia (S&P 500, CAC 40), Yahoo | seed + à la demande |
| `market_data` | Cours de clôture bruts et ajustés (dividendes, splits), taux de change | Yahoo Finance | DAG quotidien |
| `fund_profiles` | Fiche : ISIN, émetteur, catégorie, encours, frais, description | Yahoo Finance, JustETF | DAG hebdomadaire, ou à la demande si la fiche a plus de 7 jours |
| `fund_holdings` | Principales positions (look-through) | Yahoo (ETF US), JustETF (ETF UCITS) | idem |
| `fund_exposures` | Répartitions par pays et par secteur | JustETF, Yahoo (actions) | idem |
| `portfolios`, `portfolio_transactions` | Portefeuille et journal d'opérations (prix en EUR) | utilisateur | API |
| `langchain_pg_embedding` | Segments vectorisés des fiches | construits depuis les tables ci-dessus | DAG hebdomadaire |

Règles de collecte ([app/services/market_data.py](app/services/market_data.py)) :

- **Cours ajustés** : Yahoo recalcule tout l'historique ajusté à chaque dividende. Les 5 derniers jours
  ne sont recollés que si le facteur d'ajustement est inchangé ; sinon (nouveau dividende, split ou
  trou de données), l'historique de l'actif est re-téléchargé intégralement.
- **Devises** : chaque prix est converti en EUR avec le taux du jour (EURUSD=X…, ou dernier taux connu) ;
  un actif en USD porte donc aussi le risque de change.

## Méthodologie quantitative

| Sujet | Convention retenue |
|---|---|
| Rendements | Simples, journaliers, en EUR, sur cours ajustés (dividendes réinvestis) ; 252 séances par an |
| Taux sans risque | €STR : rendement sur 12 mois glissants de l'ETF monétaire XEON.DE (biais : ses frais, ~0,1 %/an) |
| Sharpe, Sortino | Ex-post : moyenne des rendements excédentaires / écart-type (Sortino : écart à la baisse sur toutes les séances), × √252 |
| Performance réelle | TWR par valorisation quotidienne (Dietz modifié journalier, flux en cours de journée) ; XIRR base 365 ; pas d'annualisation sous un an (GIPS) |
| Risque ex-ante | Poids actuels appliqués à chaque séance historique |
| Monte Carlo | Dérive nulle par défaut (espérance de rendement nulle, μ_log = −σ²/2 : prudent, la moyenne historique étant très bruitée) ou moyenne historique des log-rendements (correction d'Itô déjà incluse), covariance estimée sur blocs de 5 séances, chocs corrélés par Cholesky, VaR/ES 95 % = −quantile 5 % du PnL |
| Stress tests | Rendement réalisé de chaque actif sur la fenêtre historique, appliqué à la valeur actuelle de la ligne ; bêtas de Dimson (décalage d'une séance) |
| Clôtures non synchrones | Paris clôture avant New York : corrélations et bêtas quotidiens sont biaisés vers zéro. Covariances du Monte Carlo estimées sur blocs de 5 séances, bêtas de Dimson |
| RAG | Seuil sur la similarité cosinus réelle (0,30, à calibrer) ; chiffres de la réponse vérifiés contre le contexte fourni au LLM |

## Démarrage (Docker)

Prérequis : Docker Desktop (Compose v2).

```bash
cp .env.example .env            # puis renseigner les mots de passe et GROQ_API_KEY
docker compose up -d --build    # API :8000, front :5173, Airflow :8080 (accessibles en local uniquement)
```

Premier chargement des données :

```bash
docker compose exec api python scripts/seed_db.py               # référentiel + 3 ans de cours
docker compose exec api python scripts/seed_vector_db.py        # fiches ETF en base + index RAG
docker compose exec api python scripts/seed_demo_portfolio.py   # facultatif : portefeuille de démonstration
```

Ensuite, Airflow met à jour les cours chaque jour ouvré et les fiches chaque lundi. Pour forcer un
rechargement complet de l'historique de cours :

```bash
curl -X POST http://localhost:8000/api/v1/pipeline/daily-market -H "Content-Type: application/json" -d "{\"full_refresh\": true}"
```

Front : http://localhost:5173 · Documentation interactive de l'API : http://localhost:8000/docs

## Développement local (uv)

```bash
uv sync                              # environnement Python à partir de uv.lock (torch en version CPU)
uv run pytest                        # tests unitaires + intégration (PostgreSQL embarqué, sans Docker)
uv run pytest -m "not integration"   # tests unitaires seulement
uv run ruff check .
```

Les tests d'intégration démarrent un vrai PostgreSQL avec pgvector (paquet `pgserver`) et exécutent
le SQL du projet : migrations, synchronisation des cours, conversion en EUR, endpoints, portefeuille.
Seuls les appels réseau (Yahoo, JustETF, LLM) et Redis y sont remplacés par des doublures.

## Principaux endpoints

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/v1/portfolio` | Portefeuille et journal d'opérations |
| POST / DELETE | `/api/v1/portfolio/transactions` | Ajouter / supprimer une opération |
| GET | `/api/v1/portfolio/performance` | Performance réelle (TWR, XIRR, benchmark) |
| POST | `/api/v1/portfolio/metrics` | Risque ex-ante de la composition actuelle |
| POST | `/api/v1/portfolio/stress-scenarios` | Stress tests historiques |
| POST | `/api/v1/simulate` | Monte Carlo (résultat par WebSocket `/ws/{client_id}`) |
| GET | `/api/v1/etf/{ticker}/look-through` | Positions d'un ETF |
| POST | `/api/v1/rag/ask` | Question à l'assistant |
| POST | `/api/v1/pipeline/daily-market`, `/api/v1/pipeline/rag-ingestion` | Pipelines de collecte |

## Limites connues

- **Sécurité** : application mono-utilisateur sans authentification, prévue pour tourner en local
  (ports liés à 127.0.0.1). Seule l'ingestion manuelle de documents RAG exige un jeton
  (`ADMIN_API_TOKEN`). Le `docker-compose.yml` est une configuration de développement (code monté, `--reload`).
- **Données** : le scraping de Yahoo Finance et de JustETF dépend de services tiers non contractuels
  (formats susceptibles de changer, données différées) ; l'historique chargé couvre 3 ans.
  JustETF ne couvrant que les ETF européens (UCITS), les ETF américains (SPY, QQQ…) n'ont pas de
  répartition par pays publiée : ils apparaissent « Non ventilé » plutôt qu'avec une hypothèse.
- **Calendriers** : les jours fériés d'une place sont comblés par le dernier cours connu. La non-synchronicité
  des clôtures est corrigée pour les covariances et les bêtas, mais pas pour la volatilité et les ratios
  calculés sur rendements quotidiens (légère sous-estimation pour un portefeuille mêlant US et Europe).
- **Actifs non cotés** (SCPI, private equity, liquidités) : valorisés à leur prix de revient, exclus de la
  performance, des stress tests et de la simulation.
- **RAG** : le seuil de similarité n'est pas calibré sur un jeu de requêtes annotées ; la vérification
  des chiffres contrôle les valeurs citées, pas le sens des phrases.

## Structure du dépôt

```
app/
  api/        FastAPI : endpoints (main.py) et schémas Pydantic
  core/       Pool de connexions PostgreSQL et migration du schéma au démarrage
  quant/      Calculs : ratios, métriques, performance réelle, Monte Carlo, stress tests
  services/   Données de marché, fiches ETF, portefeuille, géographie, RAG, LLM
  worker/     Tâches Celery (Monte Carlo)
dags/         DAG Airflow (cours quotidiens, fiches + RAG hebdomadaires)
database/     Schéma initial (init_db.sql)
frontend/     Application React (Vite)
scripts/      Chargement initial des données et portefeuille de démonstration
tests/        Tests unitaires et d'intégration (pytest)
```
