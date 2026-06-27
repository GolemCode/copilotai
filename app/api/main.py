import asyncio
import json
import os
import uuid
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as aioredis
from celery.result import AsyncResult
import numpy as np
import polars as pl
from pydantic import BaseModel

from app.api.schemas import (
    PortfolioSimulationRequest, 
    PortfolioSimulationResponse, 
    TaskSubmissionResponse, 
    TaskStatusResponse
)
from app.core.db import db_manager, get_db_connection
from app.worker.tasks import compute_risk_metrics_task

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.connect()
    yield
    await db_manager.disconnect()

app = FastAPI(title="Copilot AI - Quant Engine API", lifespan=lifespan)

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
    
    redis_client = aioredis.Redis(host="redis", port=6379, db=0, decode_responses=True)
    pubsub = redis_client.pubsub()
    
    channel_name = f"ws_channel_{client_id}"
    await pubsub.subscribe(channel_name)
    
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            
            if message is not None:
                await websocket.send_text(message["data"])
                data_dict = json.loads(message["data"])
                if data_dict.get("status") in ["success", "error"]:
                    break 
            
            await asyncio.sleep(0.01)
            
    except WebSocketDisconnect:
        print(f"Client {client_id} déconnecté brutalement.")
    except Exception as e:
        print(f"Erreur WebSocket interne : {e}")
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()
        await redis_client.close()
        try:
            await websocket.close()
        except:
            pass

@app.post("/api/v1/simulate", response_model=TaskSubmissionResponse)
async def run_simulation(
    request: PortfolioSimulationRequest,
    conn=Depends(get_db_connection)
):
    try:
        tickers = [asset.ticker for asset in request.assets]
        shares_dict = {asset.ticker: asset.shares for asset in request.assets}

        query = """
            SELECT a.ticker, m.market_date, m.adjusted_close 
            FROM market_data m
            JOIN assets a ON m.asset_id = a.id
            WHERE a.ticker = ANY($1::varchar[])
            AND m.market_date >= CURRENT_DATE - INTERVAL '3 years'
            ORDER BY m.market_date ASC;
        """
        records = await conn.fetch(query, tickers)
        
        if not records:
            raise HTTPException(status_code=404, detail="Données introuvables pour ces actifs.")

        raw_df = pl.DataFrame([dict(r) for r in records])
        
        # --- BOUCLIER 1 : Assainissement des prix bruts ---
        # On convertit les NaN flottants en véritables Nulls, et on supprime les doublons
        raw_df = raw_df.with_columns(
            pl.col("adjusted_close").fill_nan(None)
        ).unique(subset=["market_date", "ticker"], keep="last")
        
        pivoted_df = (
            raw_df.pivot(index="market_date", columns="ticker", values="adjusted_close")
            .sort("market_date")
            .fill_null(strategy="forward") 
            .drop_nulls() 
        )

        if pivoted_df.height <= 2:
            raise HTTPException(status_code=400, detail="L'historique des prix est insuffisant (moins de 2 jours communs).")

        columns_present = pivoted_df.columns
        for ticker in tickers:
            if ticker not in columns_present:
                raise HTTPException(status_code=400, detail=f"Historique incomplet pour {ticker}")

        latest_prices = pivoted_df.tail(1).to_dicts()[0]
        
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

        # --- BOUCLIER 2 : Assainissement de la matrice des rendements ---
        # On supprime strictement toute ligne contenant Infinity ou NaN
        valid_rows = np.isfinite(returns_matrix).all(axis=1)
        returns_matrix = returns_matrix[valid_rows]

        if len(returns_matrix) < 2:
            raise HTTPException(status_code=400, detail="Données corrompues : impossible de calculer une matrice de covariance valide.")

        expected_returns = np.mean(returns_matrix, axis=0)
        covariance_matrix = np.cov(returns_matrix, rowvar=False)

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

        shared_file_id = str(uuid.uuid4())
        filepath = f"/tmp/shared/{shared_file_id}.arrow"
        payload_df.write_ipc(filepath)

        task = compute_risk_metrics_task.delay(
            client_id=request.client_id,
            arrow_filepath=filepath,
            num_paths=request.num_paths,
            time_horizon_days=request.time_horizon_days,
            initial_investment=total_investment,
            strategy=request.strategy
        )

        return TaskSubmissionResponse(status="accepted", task_id=task.id)

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print("\n" + "="*50)
        print("CRASH CRITIQUE DANS L'API :")
        traceback.print_exc()
        print("="*50 + "\n")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur. Consultez les logs.")

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
    try:
        query = "SELECT ticker, name FROM assets ORDER BY ticker ASC;"
        records = await conn.fetch(query)
        
        return [{"ticker": r["ticker"], "name": r["name"]} for r in records]
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Erreur lors de la récupération des actifs : {str(e)}"
        )