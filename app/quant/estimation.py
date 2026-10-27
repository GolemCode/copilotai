"""
Estimateurs robustes à la NON-SYNCHRONICITÉ des clôtures (Paris 17 h 30, New York 22 h, heure de Paris).

Un mouvement américain survenu après la clôture de Paris n'apparaît dans les cours européens que le
lendemain : sur des rendements quotidiens, la covariance entre actifs US et européens est donc
sous-estimée (une partie passe dans la covariance décalée d'un jour), ce qui biaise vers zéro
les corrélations et les bêtas.
"""
import numpy as np
import numpy.typing as npt

WEEKLY_BLOCK = 5          # séances agrégées
MIN_WEEKLY_BLOCKS = 30    # en dessous, l'estimation hebdomadaire est trop bruitée : repli sur le quotidien


def weekly_covariance(daily_returns: npt.NDArray[np.float64], block: int = WEEKLY_BLOCK,
                      min_blocks: int = MIN_WEEKLY_BLOCKS) -> npt.NDArray[np.float64]:
    """
    Covariance JOURNALIÈRE estimée sur des rendements agrégés par blocs de `block` séances (non chevauchants),
    divisée par `block`. Sur 5 séances, l'essentiel du décalage d'un jour est capturé dans le même bloc.
    Exacte pour des log-rendements (additifs) ; bonne approximation pour des rendements simples.
    """
    r = np.asarray(daily_returns, dtype=float)
    if r.ndim == 1:
        r = r[:, None]
    n_blocks = r.shape[0] // block
    if n_blocks < min_blocks:
        cov = np.cov(r, rowvar=False)
    else:
        aggregated = r[r.shape[0] - n_blocks * block:].reshape(n_blocks, block, r.shape[1]).sum(axis=1)
        cov = np.cov(aggregated, rowvar=False) / block
    return np.atleast_2d(cov)


def dimson_beta(asset_returns: npt.NDArray[np.float64], market_returns: npt.NDArray[np.float64]) -> float:
    """
    Bêta de Dimson (1979) avec un décalage d'une séance :
    β = [cov(r_i,t, r_m,t−1) + cov(r_i,t, r_m,t) + cov(r_i,t, r_m,t+1)] / var(r_m).
    """
    a = np.asarray(asset_returns, dtype=float)
    m = np.asarray(market_returns, dtype=float)
    var_m = float(np.var(m, ddof=1))
    if var_m <= 0 or a.size < 3:
        return float("nan")
    same = np.cov(a, m, ddof=1)[0, 1]
    lag = np.cov(a[1:], m[:-1], ddof=1)[0, 1]    # le marché de la veille explique l'actif aujourd'hui
    lead = np.cov(a[:-1], m[1:], ddof=1)[0, 1]   # l'actif d'aujourd'hui annonce le marché de demain
    return float((same + lag + lead) / var_m)
