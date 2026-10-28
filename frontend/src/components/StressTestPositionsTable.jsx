import { useState, useEffect } from 'react';
import { getAssetCategory } from '../utils/assetClassifier';
import { Icon } from './Icon';
import { formatDate, getDisplayTicker, getExchangeName } from '../utils/format';

export const StressTestPositionsTable = ({
  portfolio = [],
  availableAssets = [],
  metricsData = null,
  getAssetTotalShares,
  getAssetCostBasis,
  onOpenLookThrough,
  cvar95 = null
}) => {
  const [activeScenarioId, setActiveScenarioId] = useState('worst_period');
  const [customShock, setCustomShock] = useState(-25);
  // Dernière réponse de l'API, associée à la requête qui l'a produite
  const [stressState, setStressState] = useState({ request: null, data: null, error: null });

  // Scénarios historiques calculés par l'API sur les cours stockés en base (actifs cotés détenus)
  const listedAssets = portfolio
    .filter((a) => getAssetCategory(a.ticker).type !== 'non_boursier')
    .map((a) => ({ ticker: a.ticker, shares: getAssetTotalShares ? getAssetTotalShares(a) : 0 }))
    .filter((a) => a.shares > 0);
  const stressRequest = listedAssets.length > 0 ? JSON.stringify(listedAssets) : null;

  useEffect(() => {
    if (!stressRequest) return undefined;
    let cancelled = false;
    fetch('/api/v1/portfolio/stress-scenarios', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assets: JSON.parse(stressRequest), years: 3 })
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Calcul des scénarios impossible.');
        return data;
      })
      .then((data) => { if (!cancelled) setStressState({ request: stressRequest, data, error: null }); })
      .catch((err) => {
        if (!cancelled) setStressState({ request: stressRequest, data: null, error: err.message });
      });
    return () => { cancelled = true; };
  }, [stressRequest]);

  const isStressLoading = Boolean(stressRequest) && stressState.request !== stressRequest;
  const stressData = stressRequest ? stressState.data : null;
  const stressError = stressRequest && !isStressLoading ? stressState.error : null;

  if (!portfolio || portfolio.length === 0) {
    return (
      <div className="empty-stress-positions">
        <p>Aucun actif sélectionné pour le Stress Test. Ajoutez des positions dans l'onglet Portefeuille.</p>
      </div>
    );
  }

  // 1. Valorisation de chaque position (à défaut de cours : coût d'achat, jamais un prix fictif)
  const evaluatedPositions = portfolio.map((asset) => {
    const assetDetails = availableAssets.find((a) => a.ticker === asset.ticker);
    const fullName = assetDetails ? assetDetails.name : asset.ticker;
    const breakdown = (metricsData?.asset_breakdown || []).find((b) => b.ticker === asset.ticker);
    const shares = getAssetTotalShares ? getAssetTotalShares(asset) : (parseFloat(asset.shares) || 0);
    const costBasis = getAssetCostBasis ? getAssetCostBasis(asset) : 0;
    const currentPrice = breakdown?.current_price ?? null;
    const positionValue = breakdown?.position_value ?? (currentPrice != null ? shares * currentPrice : costBasis);
    const category = getAssetCategory(asset.ticker, assetDetails);
    const isListed = category.type !== 'non_boursier';
    const beta = isListed ? (stressData?.betas?.[asset.ticker] ?? null) : null;

    return {
      asset,
      ticker: asset.ticker,
      fullName,
      shares,
      currentPrice,
      positionValue: Math.max(0, positionValue),
      category,
      isListed,
      sensitivity: beta
    };
  });

  const totalPortfolioValue = evaluatedPositions.reduce((acc, p) => acc + p.positionValue, 0);
  const listedValue = evaluatedPositions.filter((p) => p.isListed).reduce((acc, p) => acc + p.positionValue, 0);
  const betaPositions = evaluatedPositions.filter((p) => p.isListed && p.sensitivity != null);
  const betaWeightTotal = betaPositions.reduce((acc, p) => acc + p.positionValue, 0);
  const portfolioBeta = betaWeightTotal > 0
    ? betaPositions.reduce((acc, p) => acc + p.sensitivity * p.positionValue, 0) / betaWeightTotal
    : null;

  // 2. Scénarios disponibles : historiques (API), Monte Carlo (si une simulation a été lancée), choc personnalisé
  const marketTicker = stressData?.market_ticker;
  const scenarios = [
    ...(stressData?.scenarios || []).map((sc) => ({
      id: sc.id,
      kind: 'historical',
      name: sc.name,
      period: `${formatDate(sc.start_date)} → ${formatDate(sc.end_date)}`,
      desc: `${sc.description} Rendement des lignes cotées sur la période : ${sc.portfolio_return_pct.toFixed(2)}%.`,
      data: sc
    })),
    ...(cvar95 != null ? [{
      id: 'monteCarloRisk',
      kind: 'montecarlo',
      name: 'Expected Shortfall Monte Carlo',
      period: 'Simulation (ES 95%)',
      desc: 'Perte moyenne des 5% pires scénarios simulés, répartie entre les lignes cotées selon leur bêta.'
    }] : []),
    {
      id: 'custom',
      kind: 'custom',
      name: 'Choc de marché personnalisé',
      period: 'Curseur paramétrable',
      desc: marketTicker
        ? `Choc appliqué à l'indice ${marketTicker} et transmis à chaque ligne cotée selon son bêta historique.`
        : "Bêtas indisponibles (indice de référence absent de la base) : choc non transmis."
    }
  ];
  const selectedScenario = scenarios.find((s) => s.id === activeScenarioId) || scenarios[0];

  // Expected Shortfall exprimée en perte (€) rapportée à la valeur des lignes cotées simulées
  const monteCarloLossPct = listedValue > 0 && cvar95 != null ? -((Math.max(0, cvar95) / listedValue) * 100) : null;

  // 3. Choc de chaque ligne dans le scénario actif (null = non modélisé)
  const shockFor = (p) => {
    if (!p.isListed) return null;
    if (selectedScenario.kind === 'historical') return selectedScenario.data.asset_returns_pct[p.ticker] ?? null;
    if (p.sensitivity == null) return null;
    if (selectedScenario.kind === 'montecarlo') {
      // Répartition au prorata du bêta : la somme pondérée des chocs redonne l'ES du portefeuille
      return portfolioBeta ? monteCarloLossPct * (p.sensitivity / portfolioBeta) : monteCarloLossPct;
    }
    return customShock * p.sensitivity;
  };

  const stressRows = evaluatedPositions.map((p) => {
    const weightPct = totalPortfolioValue > 0 ? (p.positionValue / totalPortfolioValue) * 100 : 0;
    const shock = shockFor(p);
    const shockPct = shock ?? 0;
    const lossAmount = p.positionValue * (shockPct / 100);
    const stressedValue = Math.max(0, p.positionValue + lossAmount);

    return {
      ...p,
      weightPct,
      shockPct,
      notModeled: shock == null,
      lossAmount,
      stressedValue
    };
  });

  // Totaux consolidés
  const totalShares = stressRows.reduce((acc, r) => acc + r.shares, 0);
  const totalLossAmount = stressRows.reduce((acc, r) => acc + r.lossAmount, 0);
  const totalStressedValue = stressRows.reduce((acc, r) => acc + r.stressedValue, 0);
  const totalShockPct = totalPortfolioValue > 0 ? (totalLossAmount / totalPortfolioValue) * 100 : 0;
  const weightedBeta = portfolioBeta;
  const notModeledCount = stressRows.filter((r) => r.notModeled).length;

  return (
    <div className="stress-positions-card">
      {/* En-tête de section */}
      <div className="stress-card-header">
        <div>
          <div className="stress-title-row">
            <h4 className="chart-title">Stress tests ({portfolio.length} lignes)</h4>
            <span className="stress-badge-scenario">{selectedScenario.name}</span>
          </div>
          <p className="section-subtitle">
            Pires épisodes réellement observés dans l'historique en base{stressData ? ` (${formatDate(stressData.history_start)} → ${formatDate(stressData.history_end)})` : ''}, rejoués sur vos positions actuelles
          </p>
        </div>
      </div>

      {/* SÉLECTEUR DE SCÉNARIOS DE CRISE */}
      <div className="stress-scenarios-panel">
        <label className="stress-scenario-label">Sélectionnez un scénario :</label>
        {isStressLoading && <p className="section-subtitle">Calcul des scénarios historiques...</p>}
        {stressError && <p className="section-subtitle text-danger">{stressError}</p>}
        <div className="stress-pills-row">
          {scenarios.map((sc) => (
            <button
              key={sc.id}
              type="button"
              className={`btn-stress-scenario-pill ${selectedScenario.id === sc.id ? 'active' : ''}`}
              onClick={() => setActiveScenarioId(sc.id)}
            >
              <span className="pill-text">{sc.name}</span>
              <span className="pill-sub">{sc.period}</span>
            </button>
          ))}
        </div>

        {/* Curseur dynamique si scénario personnalisé */}
        {selectedScenario.id === 'custom' && (
          <div className="custom-shock-slider-row">
            <span className="slider-label">Choc global de marché appliqué :</span>
            <input
              type="range"
              min="-50"
              max="-5"
              step="1"
              value={customShock}
              onChange={(e) => setCustomShock(parseFloat(e.target.value))}
              className="shock-slider"
            />
            <strong className="slider-val text-danger">{customShock}%</strong>
          </div>
        )}

        {/* Description explicative du scénario */}
        <div className="scenario-explainer-box">
          <span className="explainer-icon"><Icon name="info" size={16} /></span>
          <div className="explainer-content">
            <strong>{selectedScenario.name} ({selectedScenario.period}) :</strong> {selectedScenario.desc}
            {notModeledCount > 0 && (
              <> {notModeledCount} ligne(s) sans historique ou sans bêta : non modélisée(s), choc affiché « n.m. ».</>
            )}
          </div>
        </div>
      </div>

      {/* 4 KPIs SYNTHÉTIQUES DE L'IMPACT DE STRESS */}
      <div className="stress-kpis-grid">
        <div className="stress-kpi-box border-blue">
          <span className="kpi-micro-title">Capital Initial Exposé</span>
          <strong className="kpi-main-val">
            {totalPortfolioValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
          </strong>
          <span className="kpi-hint">Valeur actuelle totale du portefeuille</span>
        </div>

        <div className="stress-kpi-box border-red">
          <span className="kpi-micro-title">Perte Estimée sous Stress</span>
          <strong className="kpi-main-val text-danger">
            {totalLossAmount.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
          </strong>
          <span className="kpi-hint">Impact net du crash sur le portefeuille</span>
        </div>

        <div className="stress-kpi-box border-amber">
          <span className="kpi-micro-title">Impact Global en %</span>
          <strong className="kpi-main-val text-danger">
            {totalShockPct.toFixed(2)}%
          </strong>
          <span className="kpi-hint">Bêta moyen des lignes cotées : {weightedBeta != null ? `${weightedBeta.toFixed(2)}x` : '—'}</span>
        </div>

        <div className="stress-kpi-box border-emerald">
          <span className="kpi-micro-title">Capital Résiduel Stressé</span>
          <strong className="kpi-main-val text-dark">
            {totalStressedValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
          </strong>
          <span className="kpi-hint">Montant restant après absorption du choc</span>
        </div>
      </div>

      {/* TABLEAU DÉTAILLÉ DU STRESS TEST */}
      <div className="table-wrapper">
        <table className="positions-table stress-matrix-table">
          <thead>
            <tr>
              <th>Actif & Bourse</th>
              <th className="center">Quantité</th>
              <th className="right">Valeur Actuelle</th>
              <th className="right">Poids (%)</th>
              <th className="center">Sensibilité (Bêta)</th>
              <th className="right">Choc Simulé</th>
              <th className="right">Perte sous Stress</th>
              <th className="right">Valeur Résiduelle</th>
              <th className="center">Composition</th>
            </tr>
          </thead>
          <tbody>
            {stressRows.map((row) => {
              const isGain = row.shockPct > 0;
              const isNeutral = row.shockPct === 0;

              return (
                <tr key={row.ticker}>
                  {/* Actif */}
                  <td>
                    <div className="table-asset-cell">
                      <div className="table-asset-header">
                        <strong className="table-asset-ticker">{getDisplayTicker(row.ticker)}</strong>
                        <span className="badge-exchange">{getExchangeName(row.ticker)}</span>
                      </div>
                      <span className="table-asset-name" title={row.fullName}>{row.fullName}</span>
                    </div>
                  </td>

                  {/* Quantité */}
                  <td className="center">
                    <strong className="quantity-num">{row.shares}</strong>
                  </td>

                  {/* Valeur actuelle */}
                  <td className="right">
                    <strong className="position-value-val">
                      {row.positionValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                    </strong>
                  </td>

                  {/* Poids % */}
                  <td className="right">
                    <span className="weight-val">{row.weightPct.toFixed(1)}%</span>
                  </td>

                  {/* Sensibilité / Bêta */}
                  <td className="center">
                    <span className="badge-beta-sens" title={marketTicker ? `Bêta historique vs ${marketTicker}` : 'Bêta indisponible'}>
                      {row.sensitivity != null ? `${row.sensitivity.toFixed(2)}x` : '—'}
                    </span>
                  </td>

                  {/* Choc appliqué en % */}
                  <td className="right">
                    <span
                      className={`stress-shock-pct ${isGain ? 'text-success' : isNeutral ? 'text-muted' : 'text-danger'}`}
                      title={row.notModeled ? "Non modélisé : pas d'historique de cours ou de bêta" : undefined}
                    >
                      {row.notModeled ? 'n.m.' : `${isGain ? '+' : ''}${row.shockPct.toFixed(1)}%`}
                    </span>
                  </td>

                  {/* Perte en € */}
                  <td className="right">
                    <strong className={`stress-loss-val ${isGain ? 'text-success' : isNeutral ? 'text-muted' : 'text-danger'}`}>
                      {isGain ? '+' : ''}{row.lossAmount.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                    </strong>
                  </td>

                  {/* Valeur résiduelle */}
                  <td className="right">
                    <strong className="stress-residual-val">
                      {row.stressedValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                    </strong>
                  </td>

                  {/* Composition */}
                  <td className="center">
                    <button
                      type="button"
                      onClick={() => onOpenLookThrough(row.ticker)}
                      className="btn-table-lookthrough"
                      title={`Voir la décomposition par transparence de ${row.ticker}`}
                    >
                      <Icon name="search" size={14} /> Composition
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="table-summary-row stress-summary-row">
              <td>
                <strong>Total Portefeuille ({stressRows.length} actifs)</strong>
              </td>
              <td className="center">
                <strong>{totalShares}</strong>
              </td>
              <td className="right">
                <div className="summary-subtext">Capital initial</div>
                <strong>{totalPortfolioValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</strong>
              </td>
              <td className="right">
                <strong>100.0%</strong>
              </td>
              <td className="center">
                <span className="badge-beta-sens">{weightedBeta != null ? `${weightedBeta.toFixed(2)}x` : '—'}</span>
              </td>
              <td className="right">
                <span className="text-danger fw-bold">{totalShockPct.toFixed(2)}%</span>
              </td>
              <td className="right">
                <div className="summary-subtext">Perte globale</div>
                <strong className="text-danger">
                  {totalLossAmount.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                </strong>
              </td>
              <td className="right">
                <div className="summary-subtext">Capital résiduel</div>
                <strong className="summary-total-val text-dark">
                  {totalStressedValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                </strong>
              </td>
              <td></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
};
