import { formatDate, formatPct } from '../utils/format';

/**
 * Indicateurs clés.
 * - performance : performance RÉELLE reconstituée à partir des transactions (TWR, XIRR, risque réalisé).
 * - summary : risque EX-ANTE de la composition actuelle (poids actuels appliqués à l'historique).
 */
export const MetricsCards = ({
  summary = null,
  performance = null,
  totalCurrentValue = null,
  totalCostBasis = null
}) => {
  if (!summary && !performance) {
    return null;
  }

  // 1. Valorisation et plus-value latente (positions détenues, prix de revient)
  const portVal = totalCurrentValue ?? summary?.final_value ?? 0;
  const costBasis = totalCostBasis ?? 0;
  const unrealizedPnl = portVal - costBasis;
  const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : null;
  const isPnlPositive = unrealizedPnl >= 0;

  // 2. Performance réelle : annualisée seulement au-delà d'un an (règle GIPS)
  const perfValue = performance
    ? (performance.is_annualized ? performance.twr_annualized_pct : performance.twr_cumulative_pct)
    : null;
  const perfSubtitle = performance
    ? (performance.is_annualized
      ? `Annualisée depuis le ${formatDate(performance.start_date)}${performance.mwr_annualized_pct != null ? ` · XIRR ${formatPct(performance.mwr_annualized_pct, true)}` : ''}`
      : `Cumulée depuis le ${formatDate(performance.start_date)} (moins d'un an : non annualisée)`)
    : 'Aucune opération sur un actif coté';

  // 3. Sharpe et volatilité : réalisés si l'historique est suffisant, sinon ex-ante
  const hasRealizedRisk = performance && performance.sharpe_ratio !== null && performance.sharpe_ratio !== undefined;
  const sharpe = hasRealizedRisk ? performance.sharpe_ratio : summary?.sharpe_ratio;
  const sharpeLabel = hasRealizedRisk ? 'Ratio de Sharpe (réalisé)' : 'Ratio de Sharpe (ex-ante)';
  const riskFree = performance?.risk_free_rate_pct ?? summary?.risk_free_rate_pct;
  const riskFreeSource = performance?.risk_free_source ?? summary?.risk_free_source;

  const volatility = hasRealizedRisk ? performance.volatility_pct : summary?.annualized_volatility_pct;
  const volatilityLabel = hasRealizedRisk ? 'Volatilité annuelle (réalisée)' : 'Volatilité annuelle (ex-ante)';
  const exAnteYears = summary?.history_days ? Math.round(summary.history_days / 252) : null;

  return (
    <div className="metrics-cards-grid metrics-cards-clean">
      {/* 1. Valorisation et plus-value latente */}
      <div className="kpi-card border-kpi-blue">
        <span className="kpi-title">Valorisation du Portefeuille</span>
        <div className="kpi-value">
          {portVal.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
        </div>
        <div className="kpi-subtitle">
          Plus-value latente : <strong className={isPnlPositive ? 'text-success' : 'text-danger'}>
            {isPnlPositive ? '+' : ''}{unrealizedPnl.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
            {unrealizedPnlPct !== null && ` (${formatPct(unrealizedPnlPct, true)})`}
          </strong>
        </div>
      </div>

      {/* 2. Performance réelle (pondérée dans le temps) */}
      <div className="kpi-card border-kpi-emerald">
        <span className="kpi-title" title="Rendement pondéré dans le temps : neutralise les apports et retraits">
          Performance réelle (TWR)
        </span>
        <div className={`kpi-value ${perfValue !== null && perfValue < 0 ? 'text-danger' : 'text-success'}`}>
          {formatPct(perfValue, true)}
        </div>
        <div className="kpi-subtitle">{perfSubtitle}</div>
      </div>

      {/* 3. Ratio de Sharpe */}
      <div className="kpi-card border-kpi-indigo">
        <span className="kpi-title">{sharpeLabel}</span>
        <div className="kpi-value text-indigo">
          {sharpe !== null && sharpe !== undefined ? sharpe.toFixed(2) : '—'}
        </div>
        <div className="kpi-subtitle" title={riskFreeSource || undefined}>
          Taux sans risque {riskFree !== null && riskFree !== undefined ? `${riskFree.toFixed(2)} %` : 'indisponible'}
          {riskFreeSource ? ` (${riskFreeSource})` : ''}
        </div>
      </div>

      {/* 4. Volatilité */}
      <div className="kpi-card border-kpi-amber">
        <span className="kpi-title">{volatilityLabel}</span>
        <div className="kpi-value text-amber">
          {formatPct(volatility)}
        </div>
        <div className="kpi-subtitle">
          {hasRealizedRisk && summary?.annualized_volatility_pct != null
            ? `Ex-ante (poids actuels, ${exAnteYears ?? '?'} ans) : ${formatPct(summary.annualized_volatility_pct)}`
            : 'Poids actuels appliqués aux rendements historiques'}
        </div>
      </div>
    </div>
  );
};
