import { useEffect, useMemo, useState } from 'react';
import { Area, ComposedChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { getDisplayTicker } from '../utils/format';
import { COLORS } from '../utils/theme';

// Graine fixe : en modifiant un montant, seule l'hypothèse change, pas le tirage aléatoire
const PROJECTION_SEED = 7;
const DEBOUNCE_MS = 300;
const PORTFOLIO_SUPPORT = 'portfolio';

// Les hypothèses de 0 à 7 % par an valent pour des actions : monétaire, or et obligations sont exclus du choix
const isEquityEtf = (etf) => /^Actions|Émergents/.test(etf.category || '');

const formatEur = (value) => `${Math.round(value).toLocaleString('fr-FR')} €`;
const formatEurShort = (value) => (
  Math.abs(value) >= 1000 ? `${(value / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 0 })} k€` : `${Math.round(value)} €`
);
const formatYears = (months) => {
  const years = months / 12;
  return years === 1 ? '1 an' : `${years.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} ans`;
};
const parseAmount = (raw) => {
  const value = Number(String(raw).replace(',', '.'));
  return Number.isFinite(value) && value > 0 ? value : 0;
};

const ProjectionTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="plan-tooltip">
      <strong>Après {formatYears(point.month)}</strong>
      <span>Total versé : {formatEur(point.invested)}</span>
      <span>Valeur médiane : {formatEur(point.p50)}</span>
      <span>1 cas sur 2 : {formatEur(point.p25)} à {formatEur(point.p75)}</span>
      <span>9 cas sur 10 : {formatEur(point.p5)} à {formatEur(point.p95)}</span>
    </div>
  );
};

/**
 * Simulateur d'achats pour débutant : « si j'investis tant au départ puis tant par mois pendant N ans,
 * que pourrais-je avoir ? ». Le résultat est une fourchette, sous trois hypothèses de rendement explicites.
 */
export const SavingsPlanSimulator = ({ portfolioAllocation }) => {
  const [initialAmount, setInitialAmount] = useState('1000');
  const [monthlyContribution, setMonthlyContribution] = useState('200');
  const [years, setYears] = useState(15);
  const [support, setSupport] = useState(null);
  const [scenarioKey, setScenarioKey] = useState('central');
  const [catalog, setCatalog] = useState([]);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/v1/etfs/popular')
      .then(r => (r.ok ? r.json() : []))
      .then(data => { if (!cancelled) setCatalog(Array.isArray(data) ? data.filter(isEquityEtf) : []); })
      .catch(() => { if (!cancelled) setCatalog([]); });
    return () => { cancelled = true; };
  }, []);

  const hasPortfolio = portfolioAllocation.length > 0;
  const selectedSupport = support ?? (hasPortfolio ? PORTFOLIO_SUPPORT : catalog[0]?.ticker ?? null);

  const assets = useMemo(() => {
    if (selectedSupport === PORTFOLIO_SUPPORT) {
      return portfolioAllocation.map(a => ({ ticker: a.ticker, weight: a.weight }));
    }
    return selectedSupport ? [{ ticker: selectedSupport, weight: 1 }] : [];
  }, [selectedSupport, portfolioAllocation]);

  const initial = parseAmount(initialAmount);
  const monthly = parseAmount(monthlyContribution);
  const request = useMemo(() => (
    assets.length > 0 && initial + monthly > 0
      ? JSON.stringify({ assets, initial_amount: initial, monthly_contribution: monthly, years, seed: PROJECTION_SEED })
      : null
  ), [assets, initial, monthly, years]);

  const [projectionState, setProjectionState] = useState({ request: null, data: null, error: null });

  useEffect(() => {
    if (!request) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      fetch('/api/v1/projection', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: request })
        .then(async r => {
          const body = await r.json().catch(() => null);
          if (!r.ok) throw new Error(body?.detail || 'Projection indisponible.');
          return body;
        })
        .then(data => { if (!cancelled) setProjectionState({ request, data, error: null }); })
        .catch(err => { if (!cancelled) setProjectionState({ request, data: null, error: err.message }); });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [request]);

  const isLoading = request !== null && projectionState.request !== request;
  // Le dernier résultat reste affiché pendant le recalcul, pour éviter un clignotement à chaque saisie
  const projection = request ? projectionState.data : null;
  const error = request && !isLoading ? projectionState.error : null;

  const scenario = projection?.scenarios.find(s => s.key === scenarioKey) ?? projection?.scenarios[0];
  const chartData = useMemo(() => (
    scenario
      ? scenario.timeline.map((point, i) => ({
        ...point,
        invested: projection.invested[i],
        band90: [point.p5, point.p95],
        band50: [point.p25, point.p75],
      }))
      : []
  ), [scenario, projection]);

  // Graduations annuelles, une dizaine au plus
  const totalYears = projection ? projection.months / 12 : 0;
  const tickStep = Math.max(1, Math.ceil(totalYears / 10));
  const yearTicks = Array.from({ length: Math.floor(totalYears / tickStep) + 1 }, (_, i) => i * tickStep * 12);
  if (projection && yearTicks[yearTicks.length - 1] !== projection.months) yearTicks.push(projection.months);

  const supportLabel = selectedSupport === PORTFOLIO_SUPPORT
    ? 'votre répartition actuelle'
    : catalog.find(c => c.ticker === selectedSupport)?.benchmark ?? selectedSupport;

  return (
    <div className="plan-simulator">
      <div className="dashboard-panel plan-inputs">
        <h3 className="section-title">Préparer mes futurs achats</h3>
        <p className="section-subtitle">
          Indiquez ce que vous pensez investir : la simulation montre ce que votre épargne pourrait devenir,
          sous forme de fourchette plutôt que d'un chiffre unique.
        </p>

        <div className="plan-form-grid">
          <label className="plan-field">
            <span className="plan-field-label">Mise de départ</span>
            <span className="plan-input-wrap">
              <input
                type="number" min="0" step="100" inputMode="decimal" className="form-input"
                value={initialAmount} onChange={e => setInitialAmount(e.target.value)}
              />
              <span className="plan-input-unit">€</span>
            </span>
          </label>

          <label className="plan-field">
            <span className="plan-field-label">Versement chaque mois</span>
            <span className="plan-input-wrap">
              <input
                type="number" min="0" step="10" inputMode="decimal" className="form-input"
                value={monthlyContribution} onChange={e => setMonthlyContribution(e.target.value)}
              />
              <span className="plan-input-unit">€</span>
            </span>
          </label>

          <label className="plan-field">
            <span className="plan-field-label">Durée : <strong>{formatYears(years * 12)}</strong></span>
            <input
              type="range" min="1" max="30" step="1" className="plan-range"
              value={years} onChange={e => setYears(Number(e.target.value))}
            />
            <span className="plan-range-scale"><span>1 an</span><span>30 ans</span></span>
          </label>

          <label className="plan-field">
            <span className="plan-field-label">Investir dans</span>
            <select
              className="form-select" value={selectedSupport ?? ''}
              onChange={e => setSupport(e.target.value)}
            >
              {hasPortfolio && (
                <option value={PORTFOLIO_SUPPORT}>Ma répartition actuelle</option>
              )}
              {catalog.map(etf => (
                <option key={etf.ticker} value={etf.ticker}>
                  {etf.benchmark} : {getDisplayTicker(etf.ticker)}{etf.pea_eligible ? ' (éligible PEA)' : ''}
                </option>
              ))}
            </select>
            {selectedSupport === PORTFOLIO_SUPPORT && (
              <span className="plan-field-hint">
                {portfolioAllocation.map(a => `${getDisplayTicker(a.ticker)} ${Math.round(a.weight)} %`).join(' · ')}
              </span>
            )}
          </label>
        </div>
      </div>

      {!request && (
        <div className="dashboard-panel plan-empty">Indiquez une mise de départ ou un versement mensuel pour lancer la projection.</div>
      )}
      {error && <div className="dashboard-panel plan-empty text-danger">{error}</div>}

      {projection && (
        <>
          <div className={`dashboard-panel plan-results ${isLoading ? 'is-updating' : ''}`}>
            <p className="plan-headline">
              En plaçant {formatEur(initial)} au départ puis {formatEur(monthly)} par mois pendant {formatYears(projection.months)},
              vous aurez versé <strong>{formatEur(projection.total_invested)}</strong>. Selon l'évolution des marchés,
              cette somme pourrait devenir :
            </p>

            <div className="plan-scenario-grid">
              {projection.scenarios.map(s => (
                <button
                  key={s.key} type="button"
                  className={`plan-scenario-card ${s.key === scenario.key ? 'active' : ''}`}
                  onClick={() => setScenarioKey(s.key)}
                >
                  <span className="plan-scenario-title">
                    Hypothèse {s.label.toLowerCase()}
                    <span className="plan-scenario-rate">{(s.annual_return * 100).toLocaleString('fr-FR')} % par an en moyenne</span>
                  </span>
                  <span className="plan-scenario-median">{formatEur(s.final.median)}</span>
                  <span className="plan-scenario-caption">valeur médiane</span>
                  <span className="plan-scenario-range">
                    9 fois sur 10 entre {formatEur(s.final.p5)} et {formatEur(s.final.p95)}
                  </span>
                  <span className="plan-scenario-risk">
                    Risque de finir sous vos versements : <strong>{Math.round(s.final.probability_below_invested * 100)} %</strong>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className={`chart-card plan-chart-card ${isLoading ? 'is-updating' : ''}`}>
            <div className="plan-chart-header">
              <div>
                <h4 className="chart-title">Évolution possible de votre épargne</h4>
                <p className="chart-micro-subtitle">Hypothèse {scenario.label.toLowerCase()}, investie dans {supportLabel}</p>
              </div>
              <div className="plan-legend">
                <span><i className="plan-swatch band90" /> 9 cas sur 10</span>
                <span><i className="plan-swatch band50" /> 1 cas sur 2</span>
                <span><i className="plan-swatch median" /> Médiane</span>
                <span><i className="plan-swatch invested" /> Total versé</span>
              </div>
            </div>

            <div style={{ width: '100%', height: 360 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData} margin={{ top: 10, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke={COLORS.grid} />
                  <XAxis
                    dataKey="month" type="number" domain={[0, projection.months]}
                    ticks={yearTicks}
                    tickFormatter={m => (m === 0 ? 'Départ' : formatYears(m))}
                    stroke={COLORS.axis} tick={{ fontSize: 12 }}
                  />
                  <YAxis tickFormatter={formatEurShort} stroke={COLORS.axis} tick={{ fontSize: 12 }} width={64} />
                  <Tooltip content={<ProjectionTooltip />} />
                  <Area isAnimationActive={false} dataKey="band90" stroke="none" fill={COLORS.accentSoft} fillOpacity={0.16} />
                  <Area isAnimationActive={false} dataKey="band50" stroke="none" fill={COLORS.accentSoft} fillOpacity={0.32} />
                  <Line isAnimationActive={false} dataKey="p50" stroke={COLORS.accent} strokeWidth={2.2} dot={false} />
                  <Line isAnimationActive={false} dataKey="invested" stroke={COLORS.text} strokeWidth={1.4} strokeDasharray="5 4" dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

        </>
      )}
    </div>
  );
};
