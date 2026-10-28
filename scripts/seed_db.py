"""
Chargement initial de la base : référentiel d'actifs (S&P 500 et CAC 40 depuis Wikipédia, ETF de
référence) puis 3 ans de cours de l'univers suivi par défaut. Relançable : la synchronisation est incrémentale.
"""
import asyncio
import os
import sys
from io import StringIO

import asyncpg
import pandas as pd
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.settings import DATABASE_URL  # mêmes variables d'environnement (DB_*) que l'API
from app.services.market_data import MARKET_REFERENCE_TICKERS, ensure_assets, ensure_schema, sync_market_data

def get_market_universe():
    assets = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # --- 1. S&P 500 (États-Unis) ---
    print("Scraping de la composition du S&P 500 via Wikipedia...")
    try:
        url_sp500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(url_sp500, headers=headers, timeout=30)
        response.raise_for_status() 
        
        tables = pd.read_html(StringIO(response.text))
        for _, row in tables[0].iterrows():
            ticker = str(row['Symbol']).replace('.', '-')
            sector = str(row.get('GICS Sector', 'Technologies & Industrie'))
            assets.append({
                "ticker": ticker,
                "name": f"{str(row['Security'])} (US)",
                "sector": sector,
                "asset_class": "Actions"
            })
            
        print("Succès : S&P 500 extrait.")
    except Exception as e:
        print(f"Échec S&P 500 : {str(e)}")

    # --- 2. CAC 40 (France) ---
    print("Scraping de la composition du CAC 40 via Wikipedia...")
    try:
        url_cac = "https://en.wikipedia.org/wiki/CAC_40"
        response_cac = requests.get(url_cac, headers=headers, timeout=30)
        response_cac.raise_for_status()
        
        cac_tables = pd.read_html(StringIO(response_cac.text))
        
        cac_df = None
        for table in cac_tables:
            if 'Ticker' in table.columns and 'Company' in table.columns:
                cac_df = table
                break
                
        if cac_df is not None:
            for _, row in cac_df.iterrows():
                raw_ticker = str(row['Ticker']).strip()
                if '.' in raw_ticker:
                    base_ticker = raw_ticker.split('.')[0]
                else:
                    base_ticker = raw_ticker
                    
                final_ticker = f"{base_ticker}.PA"
                sector = str(row.get('Sector', 'Actions Françaises'))
                assets.append({
                    "ticker": final_ticker,
                    "name": f"{str(row['Company'])} (FR)",
                    "sector": sector,
                    "asset_class": "Actions"
                })
            print(f"Succès : {len(cac_df)} actifs identifiés dans le CAC 40.")
        else:
            print("Tableau du CAC 40 introuvable.")
    except Exception as e:
        print(f"Échec CAC 40 : {str(e)}")
        
    print("Chargement du référentiel des ETFs institutionnels...")
    
    etfs = [
        # Indices Broad Market
        {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": "Actions US (Large Cap)", "asset_class": "ETF"},
        {"ticker": "QQQ", "name": "Invesco QQQ Trust", "sector": "Actions US (Nasdaq-100)", "asset_class": "ETF"},
        {"ticker": "DIA", "name": "SPDR Dow Jones Industrial Average ETF", "sector": "Actions US (Dow Jones)", "asset_class": "ETF"},
        {"ticker": "IWM", "name": "iShares Russell 2000 ETF", "sector": "Actions US (Small Cap)", "asset_class": "ETF"},
        {"ticker": "VTI", "name": "Vanguard Total Stock Market ETF", "sector": "Actions US (All Cap)", "asset_class": "ETF"},
        {"ticker": "VEA", "name": "Vanguard FTSE Developed Markets ETF", "sector": "Actions Internationales", "asset_class": "ETF"},
        {"ticker": "VWO", "name": "Vanguard FTSE Emerging Markets ETF", "sector": "Actions Marchés Émergents", "asset_class": "ETF"},
        
        # Secteurs Spécifiques & Thématiques
        {"ticker": "XLK", "name": "Technology Select Sector SPDR Fund", "sector": "Technologie", "asset_class": "ETF"},
        {"ticker": "XLF", "name": "Financial Select Sector SPDR Fund", "sector": "Finance & Banques", "asset_class": "ETF"},
        {"ticker": "XLV", "name": "Health Care Select Sector SPDR Fund", "sector": "Santé & Pharma", "asset_class": "ETF"},
        {"ticker": "XLE", "name": "Energy Select Sector SPDR Fund", "sector": "Énergie & Pétrole", "asset_class": "ETF"},
        {"ticker": "ICLN", "name": "iShares Global Clean Energy ETF", "sector": "Énergies Propres", "asset_class": "ETF"},
        {"ticker": "SMH", "name": "VanEck Semiconductor ETF", "sector": "Semi-conducteurs", "asset_class": "ETF"},
        {"ticker": "ARKK", "name": "ARK Innovation ETF", "sector": "Innovation Disruptive", "asset_class": "ETF"},
        
        # Obligations & Commodités (Pour la diversification des risques)
        {"ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "sector": "Obligations d'État US 20 ans+", "asset_class": "Obligations"},
        {"ticker": "AGG", "name": "iShares Core US Aggregate Bond ETF", "sector": "Obligations US Investment Grade", "asset_class": "Obligations"},
        {"ticker": "GLD", "name": "SPDR Gold Shares", "sector": "Matières Premières (Or Physique)", "asset_class": "Matières Premières"},
        {"ticker": "SLV", "name": "iShares Silver Trust", "sector": "Matières Premières (Argent)", "asset_class": "Matières Premières"},
        {"ticker": "USO", "name": "United States Oil Fund", "sector": "Matières Premières (Pétrole)", "asset_class": "Matières Premières"},
        
        # --- Nouveaux ETF Européens (Éligibles PEA, en EUR) ---
        {"ticker": "CW8.PA", "name": "Amundi MSCI World UCITS ETF (PEA)", "sector": "Actions Monde (Large Cap)", "asset_class": "ETF"},
        {"ticker": "WPEA.PA", "name": "iShares MSCI World Swap PEA UCITS ETF", "sector": "Actions Monde (Large Cap)", "asset_class": "ETF"},
        {"ticker": "PE500.PA", "name": "Amundi S&P 500 UCITS ETF (PEA)", "sector": "Actions US (S&P 500)", "asset_class": "ETF"},
        {"ticker": "ESE.PA", "name": "BNP Paribas Easy S&P 500 UCITS ETF", "sector": "Actions US (S&P 500)", "asset_class": "ETF"},
        {"ticker": "PUST.PA", "name": "Amundi PEA Nasdaq-100 UCITS ETF", "sector": "Actions US Tech", "asset_class": "ETF"},
        {"ticker": "XEON.DE", "name": "Xtrackers II EUR Overnight Rate Swap UCITS ETF", "sector": "Monétaire (€STR)", "asset_class": "Monétaire"},
        {"ticker": "ETZ.PA", "name": "BNP Paribas Easy Stoxx Europe 600", "sector": "Actions Europe", "asset_class": "ETF"},
        {"ticker": "MSE.PA", "name": "Amundi Euro Stoxx 50 UCITS ETF", "sector": "Actions Zone Euro", "asset_class": "ETF"},
        {"ticker": "CAC.PA", "name": "Amundi CAC 40 UCITS ETF", "sector": "Actions France (CAC 40)", "asset_class": "ETF"},
        {"ticker": "PAEEM.PA", "name": "Amundi MSCI Emerging Markets (PEA)", "sector": "Actions Émergentes", "asset_class": "ETF"},
        {"ticker": "PABZ.PA", "name": "Amundi MSCI World Climate Paris Aligned PAB", "sector": "Actions Climat PAB (Article 9)", "asset_class": "ETF"}
    ]

    return assets + etfs

async def seed_database():
    universe = get_market_universe()
    print(f"Univers d'investissement : {len(universe)} actifs réels identifiés.")

    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        print(f"Échec critique de connexion à la DB : {e}")
        return

    try:
        await ensure_schema(conn)

        # Étape 1 : enregistrement du référentiel d'actifs (avec devise de cotation déduite de la place)
        print("Étape 1 : Enregistrement de tous les actifs réels dans PostgreSQL...")
        metadata = {
            a["ticker"].upper(): {
                "name": a["name"],
                "sector": a.get("sector", "Général"),
                "asset_class": a.get("asset_class", "Actions"),
            }
            for a in universe
        }
        await ensure_assets(conn, list(metadata), metadata=metadata)
        print("Étape 1 terminée : Tous les actifs sont persistés en base.")

        # Étape 2 : historique de cours (bruts + ajustés) pour les actifs prioritaires.
        # La synchronisation est incrémentale : un actif déjà à jour n'est pas re-téléchargé,
        # un actif vierge ou ré-ajusté (dividende, split) reçoit son historique complet.
        priority_tickers = MARKET_REFERENCE_TICKERS
        print(f"Étape 2 : Synchronisation des cours pour {len(priority_tickers)} actifs majeurs...")
        ticker_ids = await ensure_assets(conn, priority_tickers)
        summary = await sync_market_data(conn, ticker_ids, full=False)
        print(
            f"  -> {summary['records_upserted']} cotations enregistrées, "
            f"{len(summary['full_refresh'])} historiques complets, échecs : {summary['failed'] or 'aucun'}"
        )

    finally:
        await conn.close()
        print("Opération d'ingestion globale terminée.")

if __name__ == "__main__":
    asyncio.run(seed_database())
