import asyncio
import json
import os
import uuid
import traceback
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as aioredis
from celery.result import AsyncResult
import numpy as np
import polars as pl
from datetime import datetime, date, timezone
import math
import yfinance as yf

from app.api.schemas import (
    PortfolioSimulationRequest,
    TaskSubmissionResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagSearchRequest,
    DocumentIngestRequest,
    PortfolioMetricsRequest,
    AssetGeoExposureResponse,
    PortfolioGeoExposureRequest,
    PortfolioGeoExposureResponse,
    TransactionCreateRequest,
    StressScenarioRequest,
    PipelineScrapeRequest
)
from app.core.db import db_manager, get_db_connection
from app.core.settings import REDIS_HOST, REDIS_PORT
from app.services.geo_exposure import get_asset_geo_exposure, get_portfolio_geo_exposure
from app.worker.tasks import compute_risk_metrics_task
from app.services.llm import ask_financial_assistant
from app.services.rag_ingestion import (
    search_similar_documents,
    ingest_custom_documents,
    index_fund_documents,
    purge_fund_documents_except,
    purge_legacy_documents
)
from app.services.fund_data import load_fund_snapshot, refresh_fund_data
from app.services.etf_screener import RAG_REFERENCE_TICKERS, get_popular_etfs
from app.quant.estimation import weekly_covariance
from app.quant.metrics import compute_portfolio_metrics
from app.quant.monte_carlo import zero_drift
from app.quant.performance import compute_holdings_history, compute_realized_performance
from app.quant.stress import compute_historical_stress
from app.services.portfolio import (
    PortfolioError,
    add_transaction,
    delete_asset,
    delete_transaction,
    get_default_portfolio_id,
    load_portfolio,
)
from app.services.look_through import get_etf_look_through
from app.services.market_data import (
    ensure_assets,
    fx_ticker_for,
    infer_currency,
    load_base_currency_panel,
    normalize_currency,
    risk_free_rate,
    sync_market_data,
    BASE_CURRENCY,
    MARKET_REFERENCE_TICKERS,
    NON_LISTED_TICKERS,
)

def _redis() -> aioredis.Redis:
    """Client Redis asynchrone (canal WebSocket et bilans des pipelines)."""
    return aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.connect()
    yield
    await db_manager.disconnect()

app = FastAPI(
    title="Copilot AI - Quant Engine & RAG API",
    description="API financière haute performance combinant simulation stochastique, métriques quantitatives et RAG avec pgvector.",
    version="1.0.0",
    lifespan=lifespan
)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    
    redis_client = _redis()
    pubsub = redis_client.pubsub()
    
    channel_name = f"ws_channel_{client_id}"
    await pubsub.subscribe(channel_name)
    
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            
            if message is not None:
                await websocket.send_text(message["data"])
                try:
                    data_dict = json.loads(message["data"])
                    if data_dict.get("status") in ["success", "error"]:
                        break 
                except Exception:
                    pass
            
            await asyncio.sleep(0.01)
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Erreur WebSocket interne : {e}")
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()
        await redis_client.close()
        try:
            await websocket.close()
        except Exception:
            pass

@app.post("/api/v1/simulate", response_model=TaskSubmissionResponse)
async def run_simulation(
    request: PortfolioSimulationRequest,
    conn=Depends(get_db_connection)
):
    """Lance la simulation de Monte Carlo (stratégies : achat-conservation ou versements programmés DCA en euros)."""
    try:
        tickers = [asset.ticker for asset in request.assets]
        shares_dict = {asset.ticker: asset.shares for asset in request.assets}

        # Cours convertis en EUR : ajustés (rendements totaux) et bruts (valorisation des positions)
        pivoted_df, close_eur_df, _ = await load_base_currency_panel(conn, tickers, years=3)

        if pivoted_df.is_empty():
            raise HTTPException(status_code=404, detail="Données introuvables pour ces actifs.")
        if pivoted_df.height <= 2:
            raise HTTPException(status_code=400, detail="Historique de prix insuffisant (moins de 2 jours communs).")

        columns_present = pivoted_df.columns
        for ticker in tickers:
            if ticker not in columns_present:
                raise HTTPException(status_code=400, detail=f"Historique incomplet pour {ticker}")

        latest_prices = close_eur_df.tail(1).to_dicts()[0]
        
        total_investment = 0.0
        calculated_weights = []
        
        for ticker in tickers:
            price = latest_prices[ticker]
            shares = shares_dict[ticker]
            position_value = price * shares
            total_investment += position_value
            calculated_weights.append(position_value)
            
        if total_investment <= 0:
            raise ValueError("Le portefeuille doit avoir une valorisation strictement positive.")

        weights = np.array(calculated_weights) / total_investment
        
        returns_df = pivoted_df.select([
            (pl.col(ticker).log() - pl.col(ticker).log().shift(1)).alias(ticker)
            for ticker in tickers
        ]).drop_nulls()

        returns_matrix = returns_df.select(tickers).to_numpy()

        valid_rows = np.isfinite(returns_matrix).all(axis=1)
        returns_matrix = returns_matrix[valid_rows]

        if len(returns_matrix) < 2:
            raise HTTPException(status_code=400, detail="Données corrompues : impossible de calculer une matrice de covariance valide.")

        # Covariance journalière estimée sur des blocs de 5 séances : neutralise la non-synchronicité
        # des clôtures Paris / New York, qui sous-estime les corrélations quotidiennes (et donc la VaR)
        covariance_matrix = weekly_covariance(returns_matrix)
        # Dérive nulle en espérance (aucune tendance supposée) : hypothèse prudente pour la VaR
        expected_returns = zero_drift(covariance_matrix)

        transfer_df = pl.DataFrame({
            "ticker": tickers,
            "expected_return": expected_returns,
            "weight": weights
        })
        
        if covariance_matrix.ndim == 0:
            covariance_matrix = np.array([[covariance_matrix.item()]])

        cov_df = pl.DataFrame(
            covariance_matrix, 
            schema=[f"cov_{t}" for t in tickers]
        )
        
        payload_df = pl.concat([transfer_df, cov_df], how="horizontal")

        shared_dir = "/tmp/shared"
        os.makedirs(shared_dir, exist_ok=True)
        shared_file_id = str(uuid.uuid4())
        filepath = f"{shared_dir}/{shared_file_id}.arrow"
        payload_df.write_ipc(filepath)

        task = compute_risk_metrics_task.delay(
            client_id=request.client_id,
            arrow_filepath=filepath,
            num_paths=request.num_paths,
            time_horizon_days=request.time_horizon_days,
            initial_investment=total_investment,
            strategy=request.strategy,
            seed=request.seed
        )

        return TaskSubmissionResponse(status="accepted", task_id=task.id)

    except HTTPException:
        raise
    except Exception as e:
        print("\n" + "="*50)
        print("CRASH CRITIQUE DANS L'API :")
        traceback.print_exc()
        print("="*50 + "\n")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur : {str(e)}")

@app.get("/api/v1/simulate/{task_id}") 
async def get_simulation_status(task_id: str):
    task_result = AsyncResult(task_id)
    
    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None
    }
    
    if task_result.successful():
        response["result"] = task_result.result
    elif task_result.failed():
        response["result"] = {"error": str(task_result.info)}
        
    return response

@app.get("/api/v1/assets")
async def get_available_assets(conn=Depends(get_db_connection)):
    """Retourne l'univers des actifs et ETF enregistrés dans PostgreSQL."""
    try:
        # Les taux de change servent aux conversions internes : ils ne sont pas proposés comme actifs
        query = """
            SELECT a.ticker, COALESCE(p.name, a.name) AS name, COALESCE(a.sector, 'N/A') AS sector,
                   COALESCE(a.asset_class, 'Actions') AS asset_class, a.currency, p.quote_type
            FROM assets a LEFT JOIN fund_profiles p ON p.asset_id = a.id
            WHERE a.ticker NOT LIKE '%=X'
            ORDER BY a.ticker ASC;
        """
        records = await conn.fetch(query)

        return [
            {
                "ticker": r["ticker"],
                "name": r["name"],
                "sector": r["sector"],
                "asset_class": r["asset_class"],
                "currency": r["currency"],
                # Type d'instrument publié par Yahoo (ETF, EQUITY...) : None si la fiche n'a pas encore été collectée
                "quote_type": r["quote_type"]
            }
            for r in records
        ]
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Erreur lors de la récupération des actifs : {str(e)}"
        )

@app.get("/api/v1/etf/{ticker}/look-through")
async def get_etf_look_through_endpoint(ticker: str, conn=Depends(get_db_connection)):
    """
    Retourne la décomposition Look-Through officielle en % d'un ETF
    avec la transparence des sous-jacents et les sources vérifiées.
    """
    try:
        data = await get_etf_look_through(conn, ticker)
        return data
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erreur Look-Through : {str(e)}")

@app.get("/api/v1/etfs/popular")
async def get_popular_etfs_endpoint(quick_only: bool = True, conn=Depends(get_db_connection)):
    """
    Catalogue d'ETF de référence enrichi des données scrapées (nom, émetteur, frais, encours en EUR).
    - quick_only=True : la sélection courte affichée dans les suggestions rapides.
    - meets_aum_threshold indique si l'encours publié dépasse 1 Md€ (None si non publié).
    """
    return await get_popular_etfs(conn, quick_only=quick_only)

# ETF utilisés comme référence de marché (le libellé indique l'indice qu'ils répliquent)
COMPARISON_ETFS = [
    {"ticker": "SPY", "name": "S&P 500 (ETF SPY)"},
    {"ticker": "CAC.PA", "name": "CAC 40 (ETF Amundi)"},
    {"ticker": "QQQ", "name": "Nasdaq 100 (ETF QQQ)"},
    {"ticker": "CW8.PA", "name": "MSCI World (ETF CW8)"},
    {"ticker": "GLD", "name": "Or (ETF GLD)"},
    {"ticker": "AGG", "name": "Obligations US (ETF AGG)"},
]

@app.post("/api/v1/portfolio/metrics")
async def calculate_portfolio_metrics_endpoint(
    request: PortfolioMetricsRequest,
    conn=Depends(get_db_connection)
):
    """
    Métriques de risque EX-ANTE de la composition actuelle (poids actuels appliqués à l'historique) :
    volatilité, Sharpe, Sortino, drawdown, comparaison au benchmark. La performance réellement
    obtenue (à partir des transactions) est servie par GET /api/v1/portfolio/performance.
    """
    try:
        if not request.assets:
            raise HTTPException(status_code=400, detail="Le portefeuille ne contient aucun actif.")

        tickers = [a.ticker for a in request.assets]
        shares_dict = {a.ticker: a.shares for a in request.assets}
        benchmark_ticker = request.benchmark_ticker or "SPY"
        
        all_tickers = list(set(tickers + [benchmark_ticker]))

        # Portefeuille ET benchmark en EUR : la comparaison intègre le risque de change d'un actif en USD
        pivoted_df, close_eur_df, _ = await load_base_currency_panel(conn, all_tickers, years=request.years)

        if pivoted_df.is_empty():
            raise HTTPException(status_code=404, detail="Historique introuvable pour les actifs sélectionnés.")

        for t in tickers:
            if t not in pivoted_df.columns:
                raise HTTPException(status_code=400, detail=f"Données manquantes pour {t}")

        # Valorisation au dernier cours BRUT converti en EUR (le cours ajusté n'est pas un prix de marché)
        latest_prices = close_eur_df.tail(1).to_dicts()[0]
        total_inv = 0.0
        pos_values = []
        for t in tickers:
            p = latest_prices[t]
            val = p * shares_dict[t]
            total_inv += val
            pos_values.append(val)

        if total_inv <= 0:
            raise HTTPException(status_code=400, detail="Valeur totale du portefeuille nulle ou négative.")

        weights = np.array(pos_values) / total_inv
        benchmark_col = benchmark_ticker if benchmark_ticker in pivoted_df.columns else None
        rf = await _resolve_risk_free_rate(conn, request.risk_free_rate)

        metrics_results = compute_portfolio_metrics(
            daily_prices_df=pivoted_df,
            tickers=tickers,
            weights=weights,
            initial_investment=total_inv,
            risk_free_rate=rf["rate"],
            benchmark_col=benchmark_col,
            target_final_value=total_inv
        )
        metrics_results["summary"]["risk_free_source"] = rf["source"]

        # Enrichissement avec le cours actuel unitaire
        for item in metrics_results.get("asset_breakdown", []):
            t = item["ticker"]
            if t in latest_prices:
                item["current_price"] = round(float(latest_prices[t]), 2)

        # ETF de référence pour le carrousel "Comparer à" : on affiche le cours réel de l'ETF
        # (dans sa devise de cotation) et sa variation, et non un niveau d'indice reconstitué.
        try:
            bench_records = await conn.fetch("""
                WITH ranked AS (
                    SELECT a.ticker, a.currency, m.market_date, m.close_price, m.adjusted_close,
                           ROW_NUMBER() OVER (PARTITION BY a.ticker ORDER BY m.market_date DESC) as rn
                    FROM market_data m
                    JOIN assets a ON m.asset_id = a.id
                    WHERE a.ticker = ANY($1::varchar[])
                )
                SELECT ticker, currency, market_date, close_price, adjusted_close, rn
                FROM ranked
                WHERE rn <= 2
                ORDER BY ticker, rn ASC;
            """, [cfg["ticker"] for cfg in COMPARISON_ETFS])
            bench_data: Dict[str, List[Any]] = {}
            for r in bench_records:
                bench_data.setdefault(r["ticker"], []).append(r)

            comparison_benchmarks = []
            for cfg in COMPARISON_ETFS:
                rows = bench_data.get(cfg["ticker"], [])
                if not rows:
                    continue  # pas de donnée : on n'affiche pas de valeur inventée
                latest = float(rows[0]["close_price"])
                if len(rows) >= 2:
                    # Variation sur cours ajustés : un détachement de dividende n'est pas une baisse
                    prev_adj, latest_adj = float(rows[1]["adjusted_close"]), float(rows[0]["adjusted_close"])
                    chg_pct = (latest_adj / prev_adj - 1.0) * 100.0 if prev_adj > 0 else 0.0
                else:
                    chg_pct = 0.0
                comparison_benchmarks.append({
                    "id": cfg["ticker"],
                    "name": cfg["name"],
                    "symbol": cfg["ticker"],
                    "ticker": cfg["ticker"],
                    "currency": rows[0]["currency"] or infer_currency(cfg["ticker"]),
                    "value": round(latest, 2),
                    "change_abs": round(latest * chg_pct / (100.0 + chg_pct), 2),
                    "change_pct": round(chg_pct, 2),
                    "as_of": str(rows[0]["market_date"]),
                })
            metrics_results["comparison_benchmarks"] = comparison_benchmarks
        except Exception as be:
            print(f"Avertissement indices comparatifs: {be}")

        return metrics_results

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erreur de calcul des métriques : {str(e)}")

async def _resolve_risk_free_rate(conn, explicit: Optional[float]) -> Dict[str, Any]:
    """Taux fourni explicitement par l'appelant, sinon €STR sur 12 mois (XEON.DE en base)."""
    if explicit is not None:
        return {"rate": float(explicit), "source": "valeur fournie par l'appelant", "as_of": None}
    return await risk_free_rate(conn)


# --- Portefeuille persistant (PostgreSQL) ---

@app.get("/api/v1/portfolio")
async def get_portfolio_endpoint(conn=Depends(get_db_connection)):
    """Portefeuille enregistré en base : lignes et journal des opérations (prix en EUR)."""
    pid = await get_default_portfolio_id(conn)
    return await load_portfolio(conn, pid)


@app.post("/api/v1/portfolio/transactions")
async def add_portfolio_transaction_endpoint(request: TransactionCreateRequest, conn=Depends(get_db_connection)):
    """
    Enregistre un achat ou une vente. Sans prix saisi, le cours de clôture du jour (converti en EUR)
    est utilisé pour un actif coté. Une vente supérieure à la quantité détenue à sa date est refusée.
    """
    pid = await get_default_portfolio_id(conn)
    try:
        await add_transaction(conn, pid, request.ticker, request.date, request.type, request.shares, request.price)
    except PortfolioError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await load_portfolio(conn, pid)


@app.get("/api/v1/portfolio/performance")
async def portfolio_performance_endpoint(benchmark: str = "CW8.PA", conn=Depends(get_db_connection)):
    """
    Performance RÉELLE du portefeuille enregistré, reconstituée à partir des transactions :
    TWR (pondéré dans le temps), MWR / XIRR (pondéré par les capitaux), volatilité, Sharpe, Sortino,
    drawdown, et comparaison à un benchmark recevant les mêmes versements. Valeurs en EUR.
    """
    pid = await get_default_portfolio_id(conn)
    rows = await conn.fetch("""
        SELECT ticker, trade_date, side, quantity, price_eur
        FROM portfolio_transactions WHERE portfolio_id = $1
        ORDER BY trade_date, id;
    """, pid)
    transactions = [dict(r) for r in rows if r["ticker"] not in NON_LISTED_TICKERS]
    excluded = sorted({r["ticker"] for r in rows if r["ticker"] in NON_LISTED_TICKERS})
    if not transactions:
        return {"summary": None, "benchmark": None, "time_series": [], "excluded_non_listed": excluded,
                "message": "Aucune opération sur un actif coté : performance non calculable."}

    first_trade = min(t["trade_date"] for t in transactions)
    years = min(10, max(1, math.ceil((date.today() - first_trade).days / 365.25)))
    tickers = sorted({t["ticker"] for t in transactions})
    panel_tickers = tickers + ([benchmark] if benchmark not in tickers else [])
    adjusted, close, _ = await load_base_currency_panel(conn, panel_tickers, years=years)
    if adjusted.is_empty():
        raise HTTPException(status_code=404, detail="Historique de cours introuvable pour les actifs du portefeuille.")

    rf = await risk_free_rate(conn)
    try:
        result = compute_realized_performance(transactions, adjusted, close, benchmark=benchmark, risk_free_rate=rf["rate"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result["summary"]["risk_free_source"] = rf["source"]
    result["excluded_non_listed"] = excluded
    return result


@app.get("/api/v1/portfolio/holdings-history")
async def portfolio_holdings_history_endpoint(
    years: int = 3, benchmark: str = "CW8.PA", conn=Depends(get_db_connection)
):
    """
    Valeur historique des positions actuelles (quantités détenues aujourd'hui × cours passés, en EUR),
    en continu sur la période demandée, avec le benchmark ramené à la même valeur de départ.
    """
    pid = await get_default_portfolio_id(conn)
    rows = await conn.fetch("""
        SELECT ticker, SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) AS quantity
        FROM portfolio_transactions WHERE portfolio_id = $1
        GROUP BY ticker HAVING SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) > 1e-9;
    """, pid)
    quantities = {r["ticker"]: float(r["quantity"]) for r in rows if r["ticker"] not in NON_LISTED_TICKERS}
    excluded = sorted(r["ticker"] for r in rows if r["ticker"] in NON_LISTED_TICKERS)
    if not quantities:
        return {"time_series": [], "quantities": {}, "excluded_non_listed": excluded}

    tickers = sorted(quantities)
    panel_tickers = tickers + ([benchmark] if benchmark not in tickers else [])
    _, close, _ = await load_base_currency_panel(conn, panel_tickers, years=min(10, max(1, years)))
    return {
        "time_series": compute_holdings_history(quantities, close, benchmark=benchmark),
        "quantities": quantities,
        "excluded_non_listed": excluded,
    }


@app.delete("/api/v1/portfolio/transactions/{transaction_id}")
async def delete_portfolio_transaction_endpoint(transaction_id: int, conn=Depends(get_db_connection)):
    pid = await get_default_portfolio_id(conn)
    try:
        await delete_transaction(conn, pid, transaction_id)
    except PortfolioError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await load_portfolio(conn, pid)


@app.delete("/api/v1/portfolio/assets/{ticker}")
async def delete_portfolio_asset_endpoint(ticker: str, conn=Depends(get_db_connection)):
    """Retire une ligne du portefeuille (et toutes ses opérations)."""
    pid = await get_default_portfolio_id(conn)
    await delete_asset(conn, pid, ticker)
    return await load_portfolio(conn, pid)


@app.post("/api/v1/portfolio/stress-scenarios")
async def stress_scenarios_endpoint(request: StressScenarioRequest, conn=Depends(get_db_connection)):
    """
    Rejoue sur le portefeuille actuel les pires épisodes de l'historique stocké en base
    (pire baisse, pire mois du portefeuille, pire mois du marché) et calcule le bêta de chaque ligne.
    Les actifs non cotés n'ont pas d'historique : ils sont exclus du calcul et signalés.
    """
    try:
        shares = {a.ticker: a.shares for a in request.assets if a.ticker not in NON_LISTED_TICKERS}
        excluded = [a.ticker for a in request.assets if a.ticker in NON_LISTED_TICKERS]
        if not shares:
            raise HTTPException(status_code=400, detail="Aucun actif coté : pas d'historique sur lequel rejouer des scénarios.")

        tickers = list(shares)
        panel_tickers = tickers + ([request.market_ticker] if request.market_ticker not in tickers else [])
        adjusted, close, _ = await load_base_currency_panel(conn, panel_tickers, years=request.years)
        if adjusted.is_empty():
            raise HTTPException(status_code=404, detail="Historique introuvable pour ces actifs.")
        missing = [t for t in tickers if t not in adjusted.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"Historique manquant pour : {', '.join(missing)}")

        latest = close.tail(1).to_dicts()[0]
        values = np.array([latest[t] * shares[t] for t in tickers])
        weights = values / values.sum()

        result = compute_historical_stress(adjusted, tickers, weights, market_ticker=request.market_ticker)
        result["excluded_non_listed"] = excluded
        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erreur de calcul des scénarios de stress : {str(e)}")

# --- Endpoints RAG ---

@app.post("/api/rag/ask", response_model=RagQueryResponse)
@app.post("/api/v1/rag/ask", response_model=RagQueryResponse)
async def ask_rag_endpoint(request: RagQueryRequest):
    """
    Interroge l'assistant financier augmenté par RAG.
    Effectue une recherche cosinus dans pgvector et synthétise une réponse sourcée via Groq.
    """
    try:
        result = await ask_financial_assistant(
            user_query=request.query,
            portfolio_context=request.portfolio_context,
            chat_history=request.chat_history
        )
        return RagQueryResponse(
            answer=result["answer"],
            sources=result.get("sources", []),
            latency_ms=result.get("latency_ms"),
            suggested_questions=result.get("suggested_questions", []),
            unverified_numbers=result.get("unverified_numbers", [])
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/rag/search")
async def rag_search_endpoint(request: RagSearchRequest):
    """Effectue une recherche directe par similarité cosinus dans pgvector."""
    try:
        results = search_similar_documents(request.query, k=request.k)
        return {"query": request.query, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/rag/ingest")
async def rag_ingest_documents_endpoint(
    request: DocumentIngestRequest,
    x_admin_token: Optional[str] = Header(default=None)
):
    """
    Ingère et vectorise des documents personnalisés. Réservé à l'administrateur : un document
    injecté dans la base vectorielle est ensuite lu par le LLM (risque d'injection de prompt).
    Désactivé si la variable ADMIN_API_TOKEN n'est pas définie.
    """
    expected = os.getenv("ADMIN_API_TOKEN")
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Ingestion réservée à l'administrateur (en-tête X-Admin-Token).")
    try:
        raw_docs = [doc.model_dump() for doc in request.documents]
        count = ingest_custom_documents(raw_docs)
        return {"status": "success", "chunks_ingested": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Endpoints de Pipeline & Automatisation du Scraping ---


@app.post("/api/v1/pipeline/daily-market")
async def run_daily_market_pipeline_endpoint(
    request: Optional[PipelineScrapeRequest] = None,
    conn=Depends(get_db_connection)
):
    """
    Pipeline quotidien de collecte des cotations (déclenché par Airflow).
    1. Cible les actifs déjà suivis (ayant un historique) + l'univers de référence + les taux de change utiles.
    2. Télécharge les 5 derniers jours en un appel groupé (cours bruts ET ajustés).
    3. Recolle ces cours à l'historique seulement si le facteur d'ajustement est inchangé ;
       sinon (dividende, split, trou de données) re-télécharge l'historique complet de l'actif.
    4. Enregistre le bilan d'exécution dans Redis.
    """
    try:
        req_tickers = request.tickers if request and request.tickers else None

        # 1. Univers à mettre à jour : on ne télécharge pas les ~500 actifs du référentiel sans historique
        if req_tickers:
            base_tickers = {t.strip().upper() for t in req_tickers}
        else:
            tracked = await conn.fetch("""
                SELECT DISTINCT a.ticker FROM assets a
                JOIN market_data m ON m.asset_id = a.id;
            """)
            base_tickers = {r["ticker"] for r in tracked} | set(MARKET_REFERENCE_TICKERS)

        currencies = {t: infer_currency(t) for t in base_tickers}
        rows = await conn.fetch("SELECT ticker, currency FROM assets WHERE ticker = ANY($1::varchar[]);", list(base_tickers))
        currencies.update({r["ticker"]: r["currency"] for r in rows if r["currency"]})
        fx_tickers = {
            fx_ticker_for(normalize_currency(c)[0]) for c in currencies.values()
            if c and normalize_currency(c)[0] != BASE_CURRENCY
        }
        tickers_to_process = sorted(base_tickers | fx_tickers)

        # 2-3. Résolution des actifs puis synchronisation des cours
        ticker_id_map = await ensure_assets(conn, tickers_to_process)
        full_refresh = bool(request and request.full_refresh)
        sync_summary = await sync_market_data(conn, ticker_id_map, full=full_refresh)

        # 4. Bilan d'exécution dans Redis
        redis_client = _redis()
        scrape_summary = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assets_targeted": len(tickers_to_process),
            "records_upserted": sync_summary["records_upserted"],
            "full_refresh_count": len(sync_summary["full_refresh"]),
            "failed_count": len(sync_summary["failed"]),
            "failed_tickers": sync_summary["failed"],
        }
        await redis_client.set("pipeline:last_market_scrape", json.dumps(scrape_summary))
        await redis_client.close()

        return {
            "status": "success",
            "message": (
                f"Pipeline marché exécuté. {sync_summary['records_upserted']} cotations enregistrées pour "
                f"{len(tickers_to_process)} actifs ({len(sync_summary['full_refresh'])} historiques recalculés)."
            ),
            "data": scrape_summary
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Échec critique du pipeline de marché : {str(e)}")

@app.post("/api/v1/pipeline/rag-ingestion")
async def run_rag_ingestion_pipeline_endpoint(
    request: Optional[PipelineScrapeRequest] = None,
    conn=Depends(get_db_connection)
):
    """
    Pipeline documentaire (déclenché par Airflow) :
    1. Scrape la fiche de chaque ETF (Yahoo Finance + JustETF) et l'enregistre en base
       (fund_profiles, fund_holdings, fund_exposures).
    2. Construit les documents RAG à partir de ces données stockées (source unique).
    3. Vectorise (sentence-transformers) et remplace les segments correspondants dans pgvector.
    Lors d'un passage complet, les segments des anciennes versions du pipeline sont purgés.
    """
    try:
        full_run = not (request and request.tickers)
        if full_run:
            tracked = await conn.fetch("""
                SELECT ticker FROM portfolio_transactions
                UNION
                SELECT a.ticker FROM assets a JOIN fund_profiles p ON p.asset_id = a.id;
            """)
            target_tickers = sorted(
                (set(RAG_REFERENCE_TICKERS) | {r["ticker"] for r in tracked}) - NON_LISTED_TICKERS
            )
        else:
            target_tickers = sorted({t.strip().upper() for t in request.tickers})

        fund_summary = await refresh_fund_data(conn, target_tickers)
        snapshots = [s for s in [await load_fund_snapshot(conn, t) for t in fund_summary["refreshed"]] if s]

        legacy_deleted = await asyncio.to_thread(purge_legacy_documents) if full_run else 0
        chunks_count = await asyncio.to_thread(index_fund_documents, snapshots)
        if full_run:
            # Fiches d'actifs retirés du référentiel ou inconnus de Yahoo : hors de l'index
            legacy_deleted += await asyncio.to_thread(purge_fund_documents_except, fund_summary["refreshed"])
            await conn.execute("""
                DELETE FROM fund_profiles p USING assets a
                WHERE p.asset_id = a.id AND a.ticker = ANY($1::varchar[]);
            """, fund_summary["failed"])

        redis_client = _redis()
        rag_summary = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers_scraped": fund_summary["refreshed"],
            "tickers_failed": fund_summary["failed"],
            "chunks_vectorized": chunks_count,
            "legacy_chunks_deleted": legacy_deleted,
        }
        await redis_client.set("pipeline:last_rag_ingestion", json.dumps(rag_summary))
        await redis_client.close()

        return {
            "status": "success",
            "message": (
                f"Ingestion terminée : {len(fund_summary['refreshed'])} fiches enregistrées en base, "
                f"{chunks_count} segments vectorisés, {len(fund_summary['failed'])} échecs."
            ),
            "data": rag_summary
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Échec de l'ingestion RAG : {str(e)}")

@app.post("/api/v1/assets/auto-scrape")
async def auto_scrape_asset_endpoint(
    ticker: str,
    conn=Depends(get_db_connection)
):
    """
    Ajoute à la demande un nouvel actif financier :
    1. Récupère ses métadonnées via Yahoo Finance (nom, secteur, classe d'actif, devise de cotation).
    2. Enregistre l'actif dans la table assets.
    3. Télécharge ses cours (incrémental si l'actif est déjà suivi, 3 ans d'historique sinon).
    """
    clean_ticker = ticker.strip().upper()
    try:
        # yfinance est bloquant : exécution hors de la boucle asyncio
        info = await asyncio.to_thread(lambda: yf.Ticker(clean_ticker).info or {})

        asset_name = info.get("longName") or info.get("shortName") or f"Actif {clean_ticker}"
        quote_type = (info.get("quoteType") or "").upper()
        asset_class = "ETF" if quote_type in ["ETF", "MUTUALFUND"] else "Actions"
        sector = info.get("sector") or info.get("category") or ("ETF Thématique" if asset_class == "ETF" else "Général")
        currency = info.get("currency") or infer_currency(clean_ticker)

        ticker_ids = await ensure_assets(conn, [clean_ticker], metadata={clean_ticker: {
            "name": asset_name, "sector": sector, "asset_class": asset_class, "currency": currency,
        }})
        has_history = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM market_data WHERE asset_id = $1);", ticker_ids[clean_ticker]
        )
        sync_summary = await sync_market_data(conn, ticker_ids, full=not has_history)
        if clean_ticker in sync_summary["failed"]:
            raise HTTPException(status_code=404, detail=f"Aucune cotation disponible pour {clean_ticker} sur Yahoo Finance.")

        # Fiche descriptive (positions, pays, secteurs) enregistrée en base puis indexée pour le RAG
        try:
            await refresh_fund_data(conn, [clean_ticker])
            snapshot = await load_fund_snapshot(conn, clean_ticker)
            if snapshot:
                await asyncio.to_thread(index_fund_documents, [snapshot])
        except Exception as fe:
            print(f"Avertissement fiche descriptive {clean_ticker} : {fe}")

        total_quotes = sync_summary["records_upserted"]
        return {
            "status": "success",
            "ticker": clean_ticker,
            "name": asset_name,
            "sector": sector,
            "asset_class": asset_class,
            "currency": currency,
            "quotes_saved": total_quotes,
            "message": f"Actif {clean_ticker} synchronisé avec {total_quotes} cotations."
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erreur lors du scraping de {clean_ticker} : {str(e)}")

@app.get("/api/v1/pipeline/status")
async def get_pipeline_status_endpoint(conn=Depends(get_db_connection)):
    """Retourne l'état complet des pipelines, de la base de données et de la fraîcheur des données."""
    try:
        redis_client = _redis()
        
        last_scrape_raw = await redis_client.get("pipeline:last_market_scrape")
        last_rag_raw = await redis_client.get("pipeline:last_rag_ingestion")
        
        await redis_client.close()

        last_scrape = json.loads(last_scrape_raw) if last_scrape_raw else None
        last_rag = json.loads(last_rag_raw) if last_rag_raw else None

        total_assets = await conn.fetchval("SELECT COUNT(*) FROM assets;")
        total_market_data = await conn.fetchval("SELECT COUNT(*) FROM market_data;")
        
        try:
            total_embeddings = await conn.fetchval("SELECT COUNT(*) FROM langchain_pg_embedding;")
        except Exception:
            total_embeddings = 0

        latest_market_date = await conn.fetchval("SELECT MAX(market_date) FROM market_data;")

        return {
            "status": "healthy",
            "database": {
                "total_assets": total_assets,
                "total_market_data_records": total_market_data,
                "latest_market_date": str(latest_market_date) if latest_market_date else None,
                "total_rag_embeddings": total_embeddings
            },
            "pipelines": {
                "last_market_scrape": last_scrape,
                "last_rag_ingestion": last_rag
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur état du pipeline : {str(e)}")


@app.post("/api/v1/portfolio/geographic-exposure", response_model=PortfolioGeoExposureResponse)
async def get_portfolio_geographic_exposure_endpoint(payload: PortfolioGeoExposureRequest, conn=Depends(get_db_connection)):
    """
    Exposition géographique par pays et macro-régions de chaque actif du portefeuille,
    lue en base (fund_exposures) ; un actif jamais collecté est scrapé à la demande.
    """
    try:
        data = await get_portfolio_geo_exposure(conn, payload.tickers)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur exposition géographique portefeuille : {str(e)}")


@app.get("/api/v1/assets/{ticker}/geographic-exposure", response_model=AssetGeoExposureResponse)
async def get_asset_geographic_exposure_endpoint(ticker: str, force_refresh: bool = False, conn=Depends(get_db_connection)):
    """
    Récupère la répartition géographique détaillée par pays et macro-région d'un actif donné.
    """
    try:
        data = await get_asset_geo_exposure(conn, ticker, force_refresh=force_refresh)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur exposition géographique pour {ticker} : {str(e)}")