import { useState, useMemo, useRef } from 'react';
import {
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import { Icon } from './Icon';
import { COLORS } from '../utils/theme';

// Formattage des dates à la française
const formatFrenchDate = (dateStr, formatType = 'short') => {
  if (!dateStr || dateStr === 'Start') return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const months = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
  const fullMonths = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
  const day = d.getDate();
  const month = months[d.getMonth()];
  const fullMonth = fullMonths[d.getMonth()];
  const year = d.getFullYear();

  if (formatType === 'dayMonth') return `${day} ${month}`;
  if (formatType === 'monthYear') return `${month} ${String(year).slice(2)}`;
  if (formatType === 'full') return `${day} ${fullMonth} ${year}`;
  return `${day} ${month}`;
};

// Onglets de Période (données EOD quotidiennes)
const PORTFOLIO_TIMEFRAMES = [
  { id: '1m', label: '1 m', title: '1 mois' },
  { id: '6m', label: '6 m', title: '6 mois' },
  { id: 'ytd', label: 'YTD', title: 'Depuis début d\'année' },
  { id: '1y', label: '1 a', title: '1 an' },
  { id: '5y', label: '5 a', title: '5 ans' },
  { id: 'max', label: 'MAX', title: 'Historique complet' }
];

// ETF de référence affichés tant que l'API n'a pas répondu : aucune valeur de marché inventée
const DEFAULT_COMPARISON_BENCHMARKS = [
  { id: 'SPY', name: 'S&P 500 (ETF SPY)', symbol: 'SPY', ticker: 'SPY', value: null, change_pct: null },
  { id: 'CAC.PA', name: 'CAC 40 (ETF Amundi)', symbol: 'CAC.PA', ticker: 'CAC.PA', value: null, change_pct: null },
  { id: 'QQQ', name: 'Nasdaq 100 (ETF QQQ)', symbol: 'QQQ', ticker: 'QQQ', value: null, change_pct: null },
  { id: 'CW8.PA', name: 'MSCI World (ETF CW8)', symbol: 'CW8.PA', ticker: 'CW8.PA', value: null, change_pct: null },
  { id: 'GLD', name: 'Or (ETF GLD)', symbol: 'GLD', ticker: 'GLD', value: null, change_pct: null },
  { id: 'AGG', name: 'Obligations US (ETF AGG)', symbol: 'AGG', ticker: 'AGG', value: null, change_pct: null }
];

/**
 * Historique RÉEL du portefeuille (reconstitué à partir des transactions) :
 * - courbe en euros : valeur détenue + dividendes perçus, apports compris ;
 * - pourcentages : indice TWR, qui neutralise les apports et retraits ;
 * - benchmark : mêmes versements investis dans l'indice de référence.
 */
// Deux vues : valeur historique des positions actuelles (continue) ou performance réelle depuis les achats
const VIEW_MODES = {
  holdings: {
    label: 'Positions actuelles',
    tag: 'Valeur historique des positions actuelles',
    tagTitle: "Quantités détenues aujourd'hui × cours de clôture passés (EUR), hors dividendes",
    subtitle: 'variation de valeur des positions actuelles',
    benchHint: "Cliquez sur un indice pour comparer : indice ramené à la même valeur de départ",
    empty: 'Ajoutez une ligne cotée au portefeuille pour afficher sa valeur historique.'
  },
  real: {
    label: 'Performance réelle',
    tag: 'Valeur réelle + dividendes perçus',
    tagTitle: 'Valeur détenue + dividendes perçus ; le pourcentage est le rendement pondéré dans le temps (TWR), hors effet des apports',
    subtitle: 'gain hors apports, performance TWR',
    benchHint: "Cliquez sur un indice pour comparer : mêmes versements investis dans l'indice",
    empty: "Ajoutez une opération sur un actif coté pour afficher l'historique réel du portefeuille."
  }
};

export const PortfolioHistoryChart = ({
  timeSeries = [],
  holdingsSeries = [],
  emptyMessage = null,
  benchmarkTicker = 'SPY',
  onPeriodChange,
  onBenchmarkChange,
  currentPeriod = 3,
  comparisonBenchmarks = []
}) => {
  const [activeTimeframe, setActiveTimeframe] = useState('1m');
  const [viewMode, setViewMode] = useState('holdings');
  const view = VIEW_MODES[viewMode];
  const series = viewMode === 'holdings' ? holdingsSeries : timeSeries;
  const [hoveredPoint, setHoveredPoint] = useState(null);
  const [isComparing, setIsComparing] = useState(false);
  const [selectedBenchmarkTicker, setSelectedBenchmarkTicker] = useState(benchmarkTicker || 'SPY');

  const carouselRef = useRef(null);

  // Benchmarks finaux
  const displayBenchmarks = useMemo(() => {
    if (comparisonBenchmarks && comparisonBenchmarks.length > 0) {
      return comparisonBenchmarks;
    }
    return DEFAULT_COMPARISON_BENCHMARKS;
  }, [comparisonBenchmarks]);

  // Découpage dynamique de la série temporelle
  const slicedSeries = useMemo(() => {
    if (!series || series.length === 0) return [];
    const n = series.length;

    switch (activeTimeframe) {
      case '1m':
        return series.slice(Math.max(0, n - 22));
      case '6m':
        return series.slice(Math.max(0, n - 128));
      case 'ytd': {
        const currentYear = new Date().getFullYear();
        const ytdIdx = series.findIndex(pt => {
          if (!pt.date || pt.date === 'Start') return false;
          return pt.date.startsWith(`${currentYear}`);
        });
        return ytdIdx >= 0 ? series.slice(ytdIdx) : series.slice(Math.max(0, n - 170));
      }
      case '1y':
        return series.slice(Math.max(0, n - 253));
      case '5y':
      case 'max':
      default:
        return series;
    }
  }, [series, activeTimeframe]);

  // Points de repère sur la période sélectionnée
  const startPoint = slicedSeries.length > 0 ? slicedSeries[0] : null;
  const endPoint = slicedSeries.length > 0 ? slicedSeries[slicedSeries.length - 1] : null;

  // Performance sur la fenêtre affichée : ratio d'indices TWR (insensible aux apports)
  const startTwr = startPoint?.twr_index ?? 100;
  const startBenchIndex = startPoint?.benchmark_index ?? null;
  const startGain = startPoint?.cumulative_gain ?? 0;

  // Données prêtes pour le tracé en euros
  const chartData = useMemo(() => {
    if (slicedSeries.length === 0) return [];

    return slicedSeries.map((pt, idx) => {
      const portPerf = startTwr > 0 ? (pt.twr_index / startTwr - 1) * 100 : 0;
      const hasBench = pt.benchmark_value !== undefined && startBenchIndex;
      const benchPerf = hasBench ? (pt.benchmark_index / startBenchIndex - 1) * 100 : 0;

      return {
        ...pt,
        displayPort: pt.portfolio_value,
        displayBench: hasBench ? pt.benchmark_value : null,
        portPerf,
        benchPerf,
        isLast: idx === slicedSeries.length - 1
      };
    });
  }, [slicedSeries, startTwr, startBenchIndex]);

  // En-tête : valeur au point survolé (ou au dernier point), gain hors apports et TWR sur la fenêtre
  const currentDisplayedValue = useMemo(() => {
    const point = hoveredPoint || endPoint;
    if (!point) {
      return { val: 0, delta: 0, pct: 0, dateLabel: null, isHovered: false };
    }
    return {
      val: point.portfolio_value,
      delta: (point.cumulative_gain ?? 0) - startGain,
      pct: startTwr > 0 ? (point.twr_index / startTwr - 1) * 100 : 0,
      dateLabel: hoveredPoint ? formatFrenchDate(hoveredPoint.date, 'full') : null,
      isHovered: Boolean(hoveredPoint)
    };
  }, [hoveredPoint, endPoint, startGain, startTwr]);

  const handleScrollCarousel = (direction) => {
    if (carouselRef.current) {
      const scrollAmount = direction === 'left' ? -240 : 240;
      carouselRef.current.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    }
  };

  const handleSelectBenchmark = (bench) => {
    if (isComparing && selectedBenchmarkTicker === bench.ticker) {
      setIsComparing(false);
    } else {
      setSelectedBenchmarkTicker(bench.ticker);
      setIsComparing(true);
      if (onBenchmarkChange) {
        onBenchmarkChange(bench.ticker);
      }
    }
  };

  const handleTimeframeChange = (tf) => {
    setActiveTimeframe(tf.id);
    if (tf.id === '5y' && onPeriodChange) {
      onPeriodChange(5);
    } else if (tf.id === '1y' && onPeriodChange) {
      onPeriodChange(1);
    } else if (onPeriodChange && currentPeriod !== 3 && ['1m', '6m', 'ytd'].includes(tf.id)) {
      onPeriodChange(3);
    }
  };

  const viewSelector = (
    <div className="chart-timeframe-segment" role="group" aria-label="Vue du graphique">
      {Object.entries(VIEW_MODES).map(([id, mode]) => (
        <button
          key={id}
          type="button"
          className={`chart-segment-btn ${viewMode === id ? 'active' : ''}`}
          onClick={() => setViewMode(id)}
          title={mode.tagTitle}
        >
          {mode.label}
        </button>
      ))}
    </div>
  );

  if (!series || series.length === 0) {
    return (
      <div className="portfolio-history-card">
        {viewSelector}
        <div className="empty-history-box">
          <p>{(viewMode === 'real' && emptyMessage) || view.empty}</p>
        </div>
      </div>
    );
  }

  const isPositive = currentDisplayedValue.pct >= 0;
  const activeTfObj = PORTFOLIO_TIMEFRAMES.find(t => t.id === activeTimeframe) || PORTFOLIO_TIMEFRAMES[0];
  const activeBenchObj = displayBenchmarks.find(b => b.ticker === selectedBenchmarkTicker) || displayBenchmarks[0];

  return (
    <div className="portfolio-history-card">
      {/* 1. EN-TÊTE INSTITUTIONNEL */}
      <div className="portfolio-chart-header">
        <div className="chart-header-main">
          <div className="chart-kpi-block">
            {viewSelector}
            <span className="chart-category-tag" title={view.tagTitle}>
              {view.tag}
            </span>
            <div className="chart-price-line">
              <span className="chart-primary-price">
                {currentDisplayedValue.val.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
              </span>
              <div className="chart-perf-badge-group">
                <span className={`chart-perf-badge ${isPositive ? 'positive' : 'negative'}`}>
                  {isPositive ? '+' : ''}{currentDisplayedValue.pct.toFixed(2)} %
                </span>
                <span className={`chart-delta-text ${isPositive ? 'positive' : 'negative'}`}>
                  {isPositive ? '+' : ''}{Math.abs(currentDisplayedValue.delta).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                </span>
              </div>
            </div>
            <div className="chart-sub-meta">
              {currentDisplayedValue.isHovered ? (
                <span>Point au <strong>{currentDisplayedValue.dateLabel}</strong></span>
              ) : (
                <span>Sur la période ({activeTfObj.title}) · {view.subtitle}</span>
              )}
              {isComparing && (
                <span className="benchmark-tag-active">
                  vs {activeBenchObj.name} ({activeBenchObj.symbol || activeBenchObj.ticker})
                  <button
                    type="button"
                    className="btn-remove-benchmark"
                    onClick={() => setIsComparing(false)}
                    title="Désactiver la comparaison"
                    aria-label="Désactiver la comparaison"
                  >
                    <Icon name="close" size={12} />
                  </button>
                </span>
              )}
            </div>
          </div>

          {/* SÉLECTEUR DE PÉRIODES EN CONTRÔLE SEGMENTÉ */}
          <div className="chart-timeframe-segment">
            {PORTFOLIO_TIMEFRAMES.map((tf) => (
              <button
                key={tf.id}
                type="button"
                className={`chart-segment-btn ${activeTimeframe === tf.id ? 'active' : ''}`}
                onClick={() => handleTimeframeChange(tf)}
                title={tf.title}
              >
                {tf.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 2. GRAPHIQUE EN EUROS */}
      <div className="portfolio-chart-body" onMouseLeave={() => setHoveredPoint(null)}>
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart
            data={chartData}
            margin={{ top: 12, right: 15, left: 0, bottom: 5 }}
            onMouseMove={(e) => {
              if (e && e.activePayload && e.activePayload.length > 0) {
                setHoveredPoint(e.activePayload[0].payload);
              }
            }}
          >
            <defs>
              <linearGradient id="fintechBlueGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.accent} stopOpacity={0.14} />
                <stop offset="100%" stopColor={COLORS.accent} stopOpacity={0} />
              </linearGradient>
            </defs>

            <CartesianGrid vertical={false} stroke={COLORS.grid} />

            <XAxis
              dataKey="date"
              tickFormatter={(val) => {
                if (['1m', '6m', 'ytd'].includes(activeTimeframe)) {
                  return formatFrenchDate(val, 'dayMonth');
                }
                return formatFrenchDate(val, 'monthYear');
              }}
              minTickGap={35}
              stroke={COLORS.axis}
              fontSize={11}
              tickLine={false}
              axisLine={{ stroke: '#e2e8f0' }}
            />

            <YAxis
              tickFormatter={(val) => `${Math.round(val).toLocaleString('fr-FR')} €`}
              stroke={COLORS.axis}
              fontSize={11}
              domain={['auto', 'auto']}
              tickLine={false}
              axisLine={false}
              width={75}
            />

            <Tooltip
              isAnimationActive={false}
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  const pt = payload[0].payload;
                  return (
                    <div className="fintech-tooltip-box">
                      <div className="fintech-tooltip-header">{formatFrenchDate(pt.date, 'full')}</div>
                      <div className="fintech-tooltip-item">
                        <span className="tooltip-legend-indicator port"></span>
                        <span className="tooltip-legend-label">Portefeuille :</span>
                        <strong className="tooltip-legend-val">
                          {pt.portfolio_value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                        </strong>
                        <span className={`tooltip-legend-badge ${pt.portPerf >= 0 ? 'pos' : 'neg'}`}>
                          ({pt.portPerf >= 0 ? '+' : ''}{pt.portPerf.toFixed(2)}%)
                        </span>
                      </div>

                      {pt.invested_capital != null && (
                        <div className="fintech-tooltip-item">
                          <span className="tooltip-legend-label">Capital net investi :</span>
                          <strong className="tooltip-legend-val">
                            {pt.invested_capital.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                          </strong>
                        </div>
                      )}

                      {isComparing && pt.displayBench !== null && (
                        <div className="fintech-tooltip-item">
                          <span className="tooltip-legend-indicator bench"></span>
                          <span className="tooltip-legend-label">{activeBenchObj.name} :</span>
                          <strong className="tooltip-legend-val">
                            {pt.displayBench.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                          </strong>
                          <span className={`tooltip-legend-badge ${pt.benchPerf >= 0 ? 'pos' : 'neg'}`}>
                            ({pt.benchPerf >= 0 ? '+' : ''}{pt.benchPerf.toFixed(2)}%)
                          </span>
                        </div>
                      )}
                    </div>
                  );
                }
                return null;
              }}
            />

            <Area
              type="monotone"
              dataKey="displayPort"
              name="Mon Portefeuille"
              stroke={COLORS.accent}
              strokeWidth={2.2}
              fill="url(#fintechBlueGradient)"
              isAnimationActive={false}
              dot={(props) => {
                const { cx, cy, index } = props;
                if (index === chartData.length - 1) {
                  return (
                    <circle
                      key={`end-dot-${index}`}
                      cx={cx}
                      cy={cy}
                      r={4.5}
                      fill={COLORS.accent}
                      stroke="#ffffff"
                      strokeWidth={2}
                    />
                  );
                }
                return null;
              }}
            />

            {isComparing && (
              <Line
                type="monotone"
                dataKey="displayBench"
                name={activeBenchObj.name}
                stroke={COLORS.benchmark}
                strokeWidth={1.8}
                dot={false}
                strokeDasharray="4 4"
                isAnimationActive={false}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* 3. SECTION BENCHMARKS DE MARCHÉ */}
      <div className="benchmarks-comparison-strip">
        <div className="benchmarks-strip-header">
          <span className="benchmarks-strip-title">Indices de référence</span>
          <span className="benchmarks-strip-hint">{view.benchHint}</span>
        </div>

        <div className="benchmarks-chips-container">
          <div className="benchmarks-chips-scroll" ref={carouselRef}>
            {displayBenchmarks.map((bench) => {
              const isSelected = isComparing && selectedBenchmarkTicker === bench.ticker;
              const isBenchPos = (bench.change_pct || 0) >= 0;

              return (
                <button
                  key={bench.id}
                  type="button"
                  className={`benchmark-chip-card ${isSelected ? 'active' : ''}`}
                  onClick={() => handleSelectBenchmark(bench)}
                  title={`Comparer avec ${bench.name}`}
                >
                  <div className="bench-chip-header">
                    <span className="bench-chip-name">{bench.name}</span>
                    <span className="bench-chip-symbol">{bench.symbol || bench.ticker}</span>
                  </div>

                  <div className="bench-chip-metric">
                    <span className="bench-chip-val" title={bench.as_of ? `Clôture du ${bench.as_of}` : undefined}>
                      {bench.value != null
                        ? `${bench.value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${bench.currency === 'USD' ? '$' : bench.currency === 'EUR' ? '€' : (bench.currency || '')}`
                        : '—'}
                    </span>
                    {bench.change_pct != null && (
                      <span className={`bench-chip-var ${isBenchPos ? 'pos' : 'neg'}`}>
                        {isBenchPos ? '+' : ''}{bench.change_pct.toFixed(2)}%
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          <button
            type="button"
            className="benchmarks-scroll-btn"
            onClick={() => handleScrollCarousel('right')}
            title="Défiler"
            aria-label="Suivant"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6"></polyline>
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
};

