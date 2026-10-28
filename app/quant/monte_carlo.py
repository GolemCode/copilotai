import numpy as np
import numpy.typing as npt

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

DRIFT_MODES = ("zero", "historical")


def select_drift(mean_log_returns, covariance_matrix, mode: str = "zero"):
    """
    Dérive journalière des log-rendements utilisée par la simulation.

    - "zero" (défaut, prudent) : espérance de rendement nulle (martingale), soit μ_log = −σ²/2.
      La moyenne historique sur quelques années est un estimateur très bruité (erreur-type σ/√années)
      et, après une période haussière, rendrait la VaR optimiste.
    - "historical" : moyenne historique des log-rendements (tendance passée prolongée).
    """
    if mode == "historical":
        return np.asarray(mean_log_returns, dtype=float)
    if mode == "zero":
        return -0.5 * np.diag(np.atleast_2d(np.asarray(covariance_matrix, dtype=float)))
    raise ValueError(f"Hypothèse de dérive inconnue : {mode} (valeurs possibles : {', '.join(DRIFT_MODES)}).")


def apply_quantitative_strategy(full_paths, initial_investment, strategy_params):
    """
    Applique la stratégie d'investissement aux trajectoires de valeur du panier initial.

    - buy_and_hold : aucun flux après l'investissement initial.
    - dca : versement d'un montant fixe en euros (params.amount_eur) toutes les
      params.frequency_days séances, investi dans le même panier d'actifs (au prix simulé du jour).

    Renvoie : valeur "linéarisée" (valeur − apports + capital initial, pour le graphique),
    valeur absolue, capital investi cumulé, et le calendrier des versements.
    """
    num_paths, time_horizon = full_paths.shape
    trade_history = []
    absolute_value = full_paths.copy()
    total_invested = np.full((num_paths, time_horizon), initial_investment, dtype=float)

    strategy_type = (strategy_params or {}).get("type", "buy_and_hold")
    params = (strategy_params or {}).get("params", {}) or {}

    if strategy_type == "dca":
        amount = float(params.get("amount_eur", 0.0))
        freq = int(params.get("frequency_days", 21))
        if amount <= 0 or freq < 1:
            raise ValueError("DCA : le montant par versement et la périodicité doivent être strictement positifs.")

        # Valeur d'une "part" du panier initial : 1,0 au départ, suit la trajectoire simulée
        unit_value = full_paths / initial_investment
        contributions = np.zeros(time_horizon)
        contributions[freq::freq] = amount
        units = initial_investment + np.cumsum(contributions / unit_value, axis=1)  # parts détenues (par trajectoire)
        absolute_value = units * unit_value
        total_invested = np.broadcast_to(initial_investment + np.cumsum(contributions), (num_paths, time_horizon)).copy()

        for day_idx in np.nonzero(contributions)[0]:
            trade_history.append({"day": int(day_idx), "action": "Versement programmé (DCA)", "amount": amount})
    elif strategy_type != "buy_and_hold":
        raise ValueError(f"Stratégie inconnue : {strategy_type} (valeurs possibles : buy_and_hold, dca).")

    # Linéarisation : on retire les apports pour lire la performance sur le même axe que le capital initial
    linearized_value = absolute_value - total_invested + initial_investment
    return linearized_value, absolute_value, total_invested, trade_history


def generate_monte_carlo_simulation(expected_returns, covariance_matrix, weights, num_paths, time_horizon_days, initial_investment, strategy=None, seed=None):
    num_assets = len(weights)
    # Générateur local (et non np.random global) : même graine => mêmes trajectoires, sans effet de bord
    rng = np.random.default_rng(seed)
    try:
        L = np.linalg.cholesky(covariance_matrix)
    except np.linalg.LinAlgError:
        # Matrice non définie positive (actifs colinéaires, historique court) : redressement des valeurs propres
        L = np.linalg.cholesky(nearest_positive_definite(np.asarray(covariance_matrix, dtype=float)))

    daily_shocks = rng.standard_normal(size=(num_assets, num_paths, time_horizon_days))
    corr_daily_shocks = np.einsum('ij,jkl->ikl', L, daily_shocks)
    # expected_returns = moyenne empirique des LOG-rendements journaliers : sous un GBM,
    # E[ln(S_t+1 / S_t)] = μ - σ²/2, la correction d'Itô est donc déjà incluse dans cette moyenne.
    # La retirer une seconde fois (expected_returns - 0.5 * σ²) biaiserait la dérive vers le bas.
    daily_drift = np.asarray(expected_returns, dtype=float).reshape(-1, 1, 1)
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

    # VaR et ES exprimées en PERTE (montant positif = perte en euros), convention réglementaire.
    # Sur le PnL (valeur finale - capital investi) : en Buy & Hold, cela vaut exactement
    # investissement initial - quantile 5 % de la valeur finale ; en DCA, le capital investi
    # varie selon la trajectoire et est ainsi correctement retranché.
    pnl_quantile_5 = float(np.quantile(final_pnls, 0.05))
    value_at_risk_95 = -pnl_quantile_5
    # Expected Shortfall : perte moyenne dans les 5 % pires scénarios (au-delà de la VaR)
    expected_shortfall_95 = -float(np.mean(final_pnls[final_pnls <= pnl_quantile_5]))

    # Histogramme du PnL final (la clé "pnl" correspond désormais réellement à un PnL)
    counts, bin_edges = np.histogram(final_pnls, bins=30)
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
        "seed": seed,
        "metrics": {
            "median_absolute": float(np.percentile(final_abs, 50)),
            "median_invested": float(np.percentile(final_inv, 50)),
            "median_pnl": float(np.percentile(final_pnls, 50))
        },
        "histogram": histogram_data,
        "trade_history": trade_history,
        "paths": paths_data
    }