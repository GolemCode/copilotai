import asyncio
import asyncpg
import yfinance as yf
import requests
import pandas as pd
from io import StringIO
import random
import numpy as np
import os
from datetime import datetime, timedelta, timezone

# On récupère les variables d'environnement, avec des valeurs par défaut
DB_USER = os.getenv("POSTGRES_USER", "admin")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
DB_NAME = os.getenv("POSTGRES_DB", "copilot_db")

# LE POINT CRITIQUE : L'hôte doit pouvoir basculer entre 'localhost' (dev) et 'db' (Docker)
DB_HOST = os.getenv("DB_HOST", "localhost") 

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"

def get_market_universe():
    assets = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # --- 1. S&P 500 (États-Unis) ---
    print("Scraping de la composition du S&P 500 via Wikipedia...")
    try:
        url_sp500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(url_sp500, headers=headers)
        response.raise_for_status() 
        
        tables = pd.read_html(StringIO(response.text))
        for _, row in tables[0].iterrows():
            ticker = str(row['Symbol']).replace('.', '-')
            assets.append({"ticker": ticker, "name": f"{str(row['Security'])} (US)"})
            
        print("Succès : S&P 500 extrait.")
    except Exception as e:
        print(f"Échec S&P 500 : {str(e)}")

    # --- 2. CAC 40 (France) ---
    print("Scraping de la composition du CAC 40 via Wikipedia...")
    try:
        url_cac = "https://en.wikipedia.org/wiki/CAC_40"
        response_cac = requests.get(url_cac, headers=headers)
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
                
                # Nettoyage intelligent : si le ticker a un point (ex: MT.AS ou AIR.PA), 
                # on ne garde que la racine avant le point
                if '.' in raw_ticker:
                    base_ticker = raw_ticker.split('.')[0]
                else:
                    base_ticker = raw_ticker
                    
                # On force le suffixe de la Bourse de Paris pour Yahoo Finance
                final_ticker = f"{base_ticker}.PA"
                
                assets.append({"ticker": final_ticker, "name": f"{str(row['Company'])} (FR)"})
            print(f"Succès : {len(cac_df)} actifs identifiés dans le CAC 40.")
        else:
            print("Tableau du CAC 40 introuvable.")
    except Exception as e:
        print(f"Échec CAC 40 : {str(e)}")
        
    print("Chargement du référentiel des ETFs institutionnels...")
    
    etfs = [
        # Indices Broad Market
        {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust"},
        {"ticker": "QQQ", "name": "Invesco QQQ Trust"},
        {"ticker": "DIA", "name": "SPDR Dow Jones Industrial Average ETF"},
        {"ticker": "IWM", "name": "iShares Russell 2000 ETF"},
        {"ticker": "VTI", "name": "Vanguard Total Stock Market ETF"},
        {"ticker": "VEA", "name": "Vanguard FTSE Developed Markets ETF"},
        {"ticker": "VWO", "name": "Vanguard FTSE Emerging Markets ETF"},
        
        # Secteurs Spécifiques & Thématiques
        {"ticker": "XLK", "name": "Technology Select Sector SPDR Fund"},
        {"ticker": "XLF", "name": "Financial Select Sector SPDR Fund"},
        {"ticker": "XLV", "name": "Health Care Select Sector SPDR Fund"},
        {"ticker": "XLE", "name": "Energy Select Sector SPDR Fund"},
        {"ticker": "ICLN", "name": "iShares Global Clean Energy ETF"},
        {"ticker": "SMH", "name": "VanEck Semiconductor ETF"},
        {"ticker": "ARKK", "name": "ARK Innovation ETF"},
        
        # Obligations & Commodités (Pour la diversification des risques)
        {"ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF"},
        {"ticker": "AGG", "name": "iShares Core US Aggregate Bond ETF"},
        {"ticker": "GLD", "name": "SPDR Gold Shares"},
        {"ticker": "SLV", "name": "iShares Silver Trust"},
        {"ticker": "USO", "name": "United States Oil Fund"},
        
        # --- Nouveaux ETF Européens (Éligibles PEA, en EUR) ---
        # Le standard absolu pour la diversification mondiale
        {"ticker": "CW8.PA", "name": "Amundi MSCI World UCITS ETF (PEA)"}, 
        
        # Le marché américain optimisé pour la fiscalité française
        {"ticker": "PE500.PA", "name": "Amundi S&P 500 UCITS ETF (PEA)"},
        
        # Le secteur technologique (NASDAQ 100)
        {"ticker": "PUST.PA", "name": "Amundi PEA Nasdaq-100 UCITS ETF"},
        
        # L'économie européenne (Les 50 plus grandes capitalisations)
        {"ticker": "MSE.PA", "name": "Amundi Euro Stoxx 50 UCITS ETF"},
        
        # Le marché français global
        {"ticker": "CAC.PA", "name": "Amundi CAC 40 UCITS ETF"},
        
        # Les marchés émergents (Chine, Inde, Taïwan...)
        {"ticker": "PAEEM.PA", "name": "Amundi MSCI Emerging Markets (PEA)"}
    ]

    return assets + etfs

async def seed_database():
    universe = get_market_universe()
    print(f"Univers d'investissement : {len(universe)} actifs à ingérer.")

    # Vérification stricte des variables d'environnement
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:password@db:5432/copilot_db")
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        print(f"Échec critique de connexion à la DB : {e}")
        return

    try:
        # On itère proprement sur la liste de dictionnaires
        for idx, asset in enumerate(universe, 1):
            ticker = asset["ticker"]
            name = asset["name"]

            print(f"[{idx}/{len(universe)}] Traitement de : {ticker} ({name})")

            # 1. Résolution de l'ID de l'actif (Création ou Récupération)
            try:
                # Upsert : insère ou met à jour si le ticker existe déjà
                asset_id = await conn.fetchval("""
                    INSERT INTO assets (ticker, name) 
                    VALUES ($1, $2) 
                    ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name 
                    RETURNING id;
                """, ticker, name)
                
                if not asset_id:
                    asset_id = await conn.fetchval("SELECT id FROM assets WHERE ticker = $1", ticker)
            except Exception as db_err:
                print(f"  -> Erreur SQL sur l'actif {ticker} : {db_err}")
                continue

            # 2. Extraction résiliente depuis Yahoo Finance
            try:
                # yfinance renvoie parfois des avertissements, progress=False nettoie la console
                df = yf.download(ticker, period="3y", progress=False)

                if df.empty:
                    print(f"  -> Avertissement : Historique vide ou actif radié pour {ticker}.")
                    continue

                # 3. Nettoyage de la structure Pandas
                df = df.reset_index()

                # Parade contre le changement d'API de yfinance
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                # Sécurisation des colonnes requises par le schéma PostgreSQL
                if 'Close' not in df.columns:
                    print(f"  -> Avertissement : Colonne 'Close' introuvable pour {ticker}.")
                    continue

                # Conversion mathématique stricte et gestion des fallbacks
                df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
                df['Adj Close'] = pd.to_numeric(df.get('Adj Close', df['Close']), errors='coerce')
                df['Volume'] = pd.to_numeric(df.get('Volume', 0), errors='coerce').fillna(0)

                # Suppression stricte des lignes où les prix sont corrompus
                df = df.dropna(subset=['Date', 'Close', 'Adj Close'])

                # Construction explicite du tuple de 5 éléments
                records = []
                for _, row in df.iterrows():
                    market_date = row['Date'].date() if hasattr(row['Date'], 'date') else row['Date']
                    close_price = float(row['Close'])
                    adjusted_close = float(row['Adj Close'])
                    volume = int(row['Volume'])
                    
                    records.append((asset_id, market_date, close_price, adjusted_close, volume))

                if not records:
                    continue

                # 4. Ingestion par lot (Bulk Insert) avec le schéma complet
                await conn.executemany("""
                    INSERT INTO market_data (asset_id, market_date, close_price, adjusted_close, volume)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (asset_id, market_date) DO NOTHING;
                """, records)

                print(f"  -> {len(records)} cotations sauvegardées.")

            except Exception as yf_err:
                print(f"  -> Échec du traitement financier pour {ticker} : {yf_err}")

    finally:
        await conn.close()
        print("Opération d'ingestion globale terminée.")

if __name__ == "__main__":
    asyncio.run(seed_database())