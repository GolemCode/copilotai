import numpy as np
import numpy.typing as npt
import polars as pl

def nearest_positive_definite(
    matrix: npt.NDArray[np.float64], 
    epsilon: float = 1e-8
) -> npt.NDArray[np.float64]:
    """
    Assure qu'une matrice de covariance est symétrique définie positive 
    en redressant ses valeurs propres négatives ou nulles.
    
    Indispensable pour éviter un crash de la décomposition de Cholesky 
    face à des données de marché colinéaires ou incomplètes.
    """
    # Calcul des valeurs et vecteurs propres
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    
    # Redressement : on force les valeurs propres sous epsilon à valoir epsilon
    adjusted_eigenvalues = np.maximum(eigenvalues, epsilon)
    
    # Reconstruction de la matrice
    adjusted_matrix = eigenvectors @ np.diag(adjusted_eigenvalues) @ eigenvectors.T
    
    # Garantie de symétrie (élimination des erreurs d'arrondi flottant)
    return (adjusted_matrix + adjusted_matrix.T) / 2.0

def calculate_risk_metrics(
    simulated_paths: npt.NDArray[np.float64],
    weights: npt.NDArray[np.float64],
    initial_investment: float = 10000.0,
    confidence_level: float = 0.05
) -> pl.DataFrame:
    """
    Extrait les prix à l'horizon, applique les pondérations du portefeuille,
    et calcule la Value at Risk et l'Expected Shortfall du capital.
    """
    
    # 1. Extraction de la distribution finale
    # Le tenseur est de dimension (M, T, N). On extrait la dernière tranche temporelle.
    # final_prices a pour dimension (M, N)
    final_prices = simulated_paths[:, -1, :]
    
    # 2. Agrégation du Portefeuille (Algèbre Linéaire)
    # Produit matriciel entre les prix finaux (M, N) et le vecteur de poids (N,).
    # Résultat : Un vecteur 1D de dimension (M,) représentant les 10 000 valeurs finales.
    portfolio_final_values = (final_prices @ weights) * initial_investment
    
    # 3. Calcul des Profits et Pertes (PnL)
    pnl = portfolio_final_values - initial_investment
    
    # 4. Bascule sur Polars pour l'analyse statistique
    df = pl.DataFrame({"pnl": pnl})
    
    # 5. Calcul vectoriel natif Polars (sans boucle for, exécution Rust)
    var_value = df.select(pl.col("pnl").quantile(confidence_level)).item()
    es_value = df.filter(pl.col("pnl") <= var_value).select(pl.col("pnl").mean()).item()
    
    return pl.DataFrame({
        "Metric": ["Value at Risk (95%)", "Expected Shortfall (95%)"],
        "Value_EUR": [var_value, es_value]
    })

def apply_quantitative_strategy(full_paths, initial_investment, strategy_params):
    num_paths, time_horizon = full_paths.shape
    trade_history = []
    
    absolute_value = full_paths.copy()
    total_invested = np.full((num_paths, time_horizon), initial_investment, dtype=float)
    
    if not strategy_params or strategy_params.get("type") == "buy_and_hold":
        linearized_value = absolute_value - total_invested + initial_investment
        return linearized_value, absolute_value, total_invested, trade_history
        
    strategy_type = strategy_params.get("type")
    params = strategy_params.get("params", {})
    
    initial_shares = float(strategy_params.get("initial_shares", 1.0))
    price_per_share = full_paths / initial_shares
    
    if strategy_type == "dca":
        volume_to_buy = params.get("volume", 1) 
        freq = max(1, params.get("frequency_days", 30))
        
        shares_bought = np.zeros_like(full_paths)
        shares_bought[:, freq::freq] = volume_to_buy
        
        # cf_spent = Nombre d'actions * Prix unitaire variable du jour J
        cf_spent = shares_bought * price_per_share
        
        cumulative_shares = initial_shares + np.cumsum(shares_bought, axis=1)
        absolute_value = cumulative_shares * price_per_share
        
        # Capital investi cumulé (somme des achats successifs au prix du marché)
        total_invested = initial_investment + np.cumsum(cf_spent, axis=1)
        
        for day_idx in np.nonzero(shares_bought[0])[0]:
            vol = int(shares_bought[0, day_idx])
            if vol > 0:
                trade_history.append({"day": int(day_idx), "action": "Achat DCA", "volume": vol, "amount": float(cf_spent[0, day_idx])})
                
    elif strategy_type == "trigger":
        action = params.get("action", "sell")
        threshold = params.get("threshold_pct", 5) / 100.0
        window = max(1, params.get("window_days", 5))
        
        trade_volume = np.maximum(1.0, np.floor(initial_shares * 0.10))
        pct_change = np.zeros_like(full_paths)
        pct_change[:, window:] = (full_paths[:, window:] - full_paths[:, :-window]) / full_paths[:, :-window]
        
        if action == "buy":
            shares_bought = np.where(pct_change <= -threshold, trade_volume, 0)
            shares_bought[:, 0] = 0
            
            cf_spent = shares_bought * price_per_share
            cumulative_shares = initial_shares + np.cumsum(shares_bought, axis=1)
            
            absolute_value = cumulative_shares * price_per_share
            
            # Ton exposition au marché augmente
            total_invested = initial_investment + np.cumsum(cf_spent, axis=1)
            
            for day_idx in np.nonzero(shares_bought[0])[0]:
                trade_history.append({"day": int(day_idx), "action": "Renforcement", "volume": int(trade_volume), "amount": float(cf_spent[0, day_idx])})
                
        else: # action == "sell"
            shares_sold = np.where(pct_change >= threshold, trade_volume, 0)
            shares_sold[:, 0] = 0
            
            cf_gained = shares_sold * price_per_share
            cumulative_shares = initial_shares - np.cumsum(shares_sold, axis=1)
            cumulative_cash = np.cumsum(cf_gained, axis=1)
            
            absolute_value = (cumulative_shares * price_per_share) + cumulative_cash
            
            # ---> LA CORRECTION EST ICI <---
            # L'argent sort du risque, donc le capital investi diminue
            total_invested = initial_investment - cumulative_cash
            
            for day_idx in np.nonzero(shares_sold[0])[0]:
                trade_history.append({"day": int(day_idx), "action": "Prise de profit", "volume": int(trade_volume), "amount": float(cf_gained[0, day_idx])})

    # LINÉARISATION
    linearized_value = absolute_value - total_invested + initial_investment

    return linearized_value, absolute_value, total_invested, trade_history


def generate_monte_carlo_simulation(expected_returns, covariance_matrix, weights, num_paths, time_horizon_days, initial_investment, strategy=None):
    num_assets = len(weights)
    try:
        L = np.linalg.cholesky(covariance_matrix)
    except np.linalg.LinAlgError:
        L = np.linalg.cholesky(covariance_matrix + np.eye(num_assets) * 1e-8)

    daily_shocks = np.random.normal(0, 1, size=(num_assets, num_paths, time_horizon_days))
    corr_daily_shocks = np.einsum('ij,jkl->ikl', L, daily_shocks)
    daily_drift = (expected_returns - 0.5 * np.diag(covariance_matrix)).reshape(-1, 1, 1)
    daily_asset_returns = np.exp(daily_drift + corr_daily_shocks)
    
    cumulative_asset_returns = np.cumprod(daily_asset_returns, axis=2)
    portfolio_paths = np.einsum('i,ijk->jk', weights, cumulative_asset_returns) * initial_investment

    full_paths = np.zeros((num_paths, time_horizon_days + 1), dtype=float)
    full_paths[:, 0] = initial_investment
    full_paths[:, 1:] = portfolio_paths

    # Exécution Stratégie
    linearized_paths, absolute_paths, total_invested_matrix, trade_history = apply_quantitative_strategy(
        full_paths=full_paths, 
        initial_investment=initial_investment, 
        strategy_params=strategy
    )

    final_abs = absolute_paths[:, -1]
    final_inv = total_invested_matrix[:, -1]
    final_pnls = final_abs - final_inv

    sorted_abs = np.sort(final_abs)
    var_index = int(0.05 * num_paths)
    value_at_risk_95 = float(sorted_abs[var_index])
    expected_shortfall_95 = float(np.mean(sorted_abs[:var_index]))

    counts, bin_edges = np.histogram(final_abs, bins=30)
    histogram_data = [{"pnl": float(np.round((bin_edges[i] + bin_edges[i+1]) / 2, 2)), "frequence": int(counts[i])} for i in range(len(counts))]

    num_paths_to_display = min(100, num_paths)
    paths_data = []
    
    for day in range(time_horizon_days + 1):
        day_data = {"day": day}
        
        # 1. Courbes Linéarisées (Graphe)
        day_data["p1"] = float(round(np.percentile(linearized_paths[:, day], 1), 2))
        day_data["median"] = float(round(np.percentile(linearized_paths[:, day], 50), 2))
        day_data["p99"] = float(round(np.percentile(linearized_paths[:, day], 99), 2))
        
        # 2. Données Absolues (Infobulle) -> VÉRIFIE QUE CES LIGNES SONT BIEN LÀ
        day_data["abs_p1"] = float(round(np.percentile(absolute_paths[:, day], 1), 2))
        day_data["abs_median"] = float(round(np.percentile(absolute_paths[:, day], 50), 2))
        day_data["abs_p99"] = float(round(np.percentile(absolute_paths[:, day], 99), 2))
        
        # 3. Données Investies (Infobulle) -> VÉRIFIE QUE CES LIGNES SONT BIEN LÀ
        day_data["inv_p1"] = float(round(np.percentile(total_invested_matrix[:, day], 1), 2))
        day_data["inv_median"] = float(round(np.percentile(total_invested_matrix[:, day], 50), 2))
        day_data["inv_p99"] = float(round(np.percentile(total_invested_matrix[:, day], 99), 2))
        
        for path_idx in range(num_paths_to_display):
            day_data[f"path_{path_idx}"] = float(round(linearized_paths[path_idx, day], 2))
            
        paths_data.append(day_data)

    return {
        "value_at_risk_95": value_at_risk_95,
        "expected_shortfall_95": expected_shortfall_95,
        "initial_investment": initial_investment,
        "metrics": {
            "median_absolute": float(np.percentile(final_abs, 50)),
            "median_invested": float(np.percentile(final_inv, 50)),
            "median_pnl": float(np.percentile(final_pnls, 50))
        },
        "histogram": histogram_data,
        "trade_history": trade_history,
        "paths": paths_data
    }