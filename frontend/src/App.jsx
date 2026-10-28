import { useState, useEffect, useCallback, useMemo } from 'react';
import { useRiskSimulation } from './hooks/useRiskSimulation';
import { FloatingCopilot } from './components/FloatingCopilot';
import { PortfolioAllocationChart } from './components/PortfolioAllocationChart';
import { PortfolioHistoryChart } from './components/PortfolioHistoryChart';
import { MetricsCards } from './components/MetricsCards';
import { LookThroughModal } from './components/LookThroughModal';
import { TransactionModal } from './components/TransactionModal';
import { ListedAllocationChart } from './components/ListedAllocationChart';
import { GeographicAllocationChart } from './components/GeographicAllocationChart';
import { StressTestPositionsTable } from './components/StressTestPositionsTable';
import { getAssetCategory } from './utils/assetClassifier';
import { Icon } from './components/Icon';
import { getDisplayTicker, getExchangeName, signTone, toneClass } from './utils/format';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Cell
} from 'recharts';
import './App.css';
import { COLORS } from './utils/theme';

const CustomSimulationTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    const rawData = payload[0].payload;
    const mainMetrics = payload.filter(entry => ['p1', 'median', 'p99'].includes(entry.dataKey));

    return (
      <div className="tooltip-container">
        <p className="tooltip-title">
          Jour J+{label}
        </p>
        {mainMetrics.map((entry, index) => {
          const dataKey = entry.dataKey;
          const finalAbs = rawData[`abs_${dataKey}`];
          const finalInv = rawData[`inv_${dataKey}`];

          if (finalAbs === undefined || finalInv === undefined) {
            return (
              <div key={index} className="tooltip-error" style={{ color: entry.color }}>
                <strong>{entry.name}</strong> : Données en cours de calcul
              </div>
            );
          }

          const netPnl = finalAbs - finalInv;
          const pnlPct = finalInv > 0 ? (netPnl / finalInv) * 100 : 0;
          const sign = netPnl >= 0 ? '+' : '';
          const pnlClass = toneClass(netPnl);

          return (
            <div key={index} className="tooltip-metric">
              <p className="tooltip-metric-main" style={{ color: entry.color }}>
                {entry.name} : {finalAbs.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
              </p>
              <p className="tooltip-metric-detail">
                 (Investi : {finalInv.toFixed(2)}€ | PnL : <span className={`${pnlClass} fw-bold`}>{sign}{netPnl.toFixed(2)}€ / {sign}{pnlPct.toFixed(2)}%</span>)
              </p>
            </div>
          );
        })}
      </div>
    );
  }
  return null;
};

// Exposition géographique : uniquement les répartitions par pays publiées (JustETF / Yahoo),
// stockées en base. Un actif sans donnée publiée est compté comme "Non ventilé".
const getAssetGeographicExposure = (ticker, dynamicGeoData = {}) => {
  const regions = dynamicGeoData?.[ticker]?.regions;
  if (regions && Object.keys(regions).length > 0) return regions;
  return { 'Non ventilé': 100 };
};

const calculateGeographicBreakdown = (currentPortfolio, breakdownList = [], totalVal = 0, getSharesFn, dynamicGeoData = {}) => {
  if (!currentPortfolio || currentPortfolio.length === 0 || totalVal <= 0) return [];

  const regionTotals = {
    'États-Unis': 0,
    'Europe / France': 0,
    'Asie & Pacifique': 0,
    'International / Autres': 0,
    'Autres pays (non détaillés)': 0,
    'Non ventilé': 0
  };

  currentPortfolio.forEach(asset => {
    const bd = breakdownList.find(b => b.ticker === asset.ticker);
    const shares = getSharesFn(asset);
    const posVal = bd?.position_value ?? (bd?.current_price ? shares * bd.current_price : 0);
    const geoWeights = getAssetGeographicExposure(asset.ticker, dynamicGeoData);

    Object.entries(geoWeights).forEach(([region, pct]) => {
      const regionVal = posVal * (pct / 100);
      if (regionTotals[region] !== undefined) {
        regionTotals[region] += regionVal;
      } else {
        regionTotals['International / Autres'] += regionVal;
      }
    });
  });

  return Object.entries(regionTotals)
    .filter(([, val]) => val > 0)
    .map(([region, amount]) => ({
      region,
      amount,
      percentage: totalVal > 0 ? (amount / totalVal) * 100 : 0
    }))
    .sort((a, b) => b.amount - a.amount);
};

// Fonctions de calcul comptable sur les opérations (Achats et Ventes, PRU pondéré et quantité nette)
const getAssetAccounting = (asset) => {
  if (!asset.transactions || asset.transactions.length === 0) {
    const fallbackShares = parseFloat(asset.shares) || 0;
    const fallbackPrice = parseFloat(asset.purchase_price) || 0;
    return {
      totalShares: fallbackShares,
      costBasis: fallbackShares * fallbackPrice,
      pru: fallbackPrice,
      realizedPnl: 0
    };
  }

  const sortedTxs = [...asset.transactions].sort((a, b) => new Date(a.date) - new Date(b.date));
  let totalShares = 0;
  let costBasis = 0;
  let pru = 0;
  let realizedPnl = 0;

  sortedTxs.forEach((t) => {
    const s = parseFloat(t.shares) || 0;
    const p = parseFloat(t.price) || 0;
    if (t.type === 'sell') {
      const sold = Math.min(totalShares, s);
      realizedPnl += sold * (p - pru);
      totalShares -= sold;
      costBasis = totalShares * pru;
    } else {
      const newTotal = totalShares + s;
      costBasis += s * p;
      pru = newTotal > 0 ? costBasis / newTotal : 0;
      totalShares = newTotal;
    }
  });

  return { totalShares, costBasis, pru, realizedPnl };
};

const getAssetTotalShares = (asset) => getAssetAccounting(asset).totalShares;
const getAssetCostBasis = (asset) => getAssetAccounting(asset).costBasis;
const getAssetPru = (asset) => getAssetAccounting(asset).pru;

const UNLISTED_PRESETS = [
  { ticker: 'SCPI-EUR', name: 'SCPI & Immobilier Papier (Non Coté)', sector: 'Immobilier Privé', asset_class: 'Non Boursier' },
  { ticker: 'PE-EUR', name: 'Private Equity / FCPR (Non Coté)', sector: 'Capital-Investissement', asset_class: 'Non Boursier' },
  { ticker: 'CASH-EUR', name: 'Fonds Euros & Liquidités (Livret A / Épargne)', sector: 'Liquidités', asset_class: 'Non Boursier' }
];
const NON_LISTED_TICKERS = UNLISTED_PRESETS.map(p => p.ticker);

function App() {
  const [activeTab, setActiveTab] = useState('portfolio'); // 'portfolio' | 'simulation'
  const [selectedLookThroughTicker, setSelectedLookThroughTicker] = useState(null);
  const [selectedAssetForTransactions, setSelectedAssetForTransactions] = useState(null);
  const [openMenuTicker, setOpenMenuTicker] = useState(null);

  // Fermeture du menu contextuel 3-points au clic extérieur
  useEffect(() => {
    const handleOutsideClick = (e) => {
      if (!e.target.closest('.table-kebab-wrapper')) {
        setOpenMenuTicker(null);
      }
    };
    if (openMenuTicker) {
      document.addEventListener('click', handleOutsideClick);
    }
    return () => {
      document.removeEventListener('click', handleOutsideClick);
    };
  }, [openMenuTicker]);

  const { startSimulation, status, result, errorMessage: simulationError } = useRiskSimulation();

  const [availableAssets, setAvailableAssets] = useState([]);
  // Portefeuille et opérations enregistrés en base (PostgreSQL) via l'API
  const [portfolio, setPortfolio] = useState([]);
  const [portfolioError, setPortfolioError] = useState(null);

  const applyPortfolioResponse = useCallback(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const message = data.detail || "Opération refusée par le serveur.";
      setPortfolioError(message);
      alert(message);
      return false;
    }
    setPortfolioError(null);
    setPortfolio(data.assets || []);
    return true;
  }, []);

  useEffect(() => {
    fetch('/api/v1/portfolio')
      .then(applyPortfolioResponse)
      .catch((err) => {
        console.error("Erreur chargement du portefeuille :", err);
        setPortfolioError("Portefeuille indisponible (API injoignable).");
      });
  }, [applyPortfolioResponse]);

  // Incrémenté après une mise à jour des cours : relance les calculs dépendant des données de marché
  const [dataRefreshTick, setDataRefreshTick] = useState(0);

  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [timeHorizon, setTimeHorizon] = useState(252);
  const [historicalYears, setHistoricalYears] = useState(3);
  const [benchmarkTicker, setBenchmarkTicker] = useState('SPY');

  // État de l'Assistant Copilot (Bulle flottante & Fenêtre modale)
  const [isCopilotOpen, setIsCopilotOpen] = useState(false);
  const [isCopilotExpanded, setIsCopilotExpanded] = useState(false);

  // Stratégies stochastiques (Buy & Hold et Versements programmés DCA)
  const [strategyType, setStrategyType] = useState('buy_and_hold');
  const [dcaAmount, setDcaAmount] = useState(200);
  const [dcaFrequency, setDcaFrequency] = useState(30);


  // 1. Chargement de l'univers d'actifs depuis l'API
  useEffect(() => {
    const fetchAssetsFromServer = async () => {
      try {
        const response = await fetch('/api/v1/assets');
        if (response.ok) {
          const data = await response.json();
          setAvailableAssets([...data, ...UNLISTED_PRESETS]);
        }
      } catch (error) {
        console.error("Erreur chargement des actifs :", error);
      }
    };
    fetchAssetsFromServer();
  }, []);

  // Chaque calcul serveur est décrit par une "requête" dérivée de l'état (chaîne JSON) :
  // l'effet ne se relance que si elle change, et l'affichage se déduit de la dernière réponse reçue.

  // 2. Métriques de risque ex-ante de la composition actuelle (lignes cotées détenues)
  const metricsRequest = useMemo(() => {
    const assets = portfolio
      .filter(a => !NON_LISTED_TICKERS.includes(a.ticker))
      .map(a => ({ ticker: a.ticker, shares: getAssetTotalShares(a) }))
      .filter(a => a.shares > 0);
    return assets.length > 0
      ? JSON.stringify({ assets, benchmark_ticker: benchmarkTicker, years: historicalYears, refresh: dataRefreshTick })
      : null;
  }, [portfolio, benchmarkTicker, historicalYears, dataRefreshTick]);
  const [metricsState, setMetricsState] = useState({ request: null, data: null });

  useEffect(() => {
    if (!metricsRequest) return undefined;
    let cancelled = false;
    const { assets, benchmark_ticker, years } = JSON.parse(metricsRequest);
    fetch('/api/v1/portfolio/metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assets, benchmark_ticker, years })
    })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (!cancelled) setMetricsState({ request: metricsRequest, data }); })
      .catch(err => {
        console.error("Erreur calcul métriques :", err);
        if (!cancelled) setMetricsState({ request: metricsRequest, data: null });
      });
    return () => { cancelled = true; };
  }, [metricsRequest]);
  const metricsData = metricsRequest ? metricsState.data : null;
  const isMetricsLoading = Boolean(metricsRequest) && metricsState.request !== metricsRequest;

  // 3. Performance réelle reconstituée à partir des transactions enregistrées (TWR, XIRR, benchmark)
  const performanceRequest = useMemo(() => (
    portfolio.length > 0
      ? JSON.stringify({
        benchmark: benchmarkTicker,
        refresh: dataRefreshTick,
        transactions: portfolio.map(a => [a.ticker, (a.transactions || []).map(t => t.id)])
      })
      : null
  ), [portfolio, benchmarkTicker, dataRefreshTick]);
  const [performanceState, setPerformanceState] = useState({ request: null, data: null });

  useEffect(() => {
    if (!performanceRequest) return undefined;
    let cancelled = false;
    const { benchmark } = JSON.parse(performanceRequest);
    fetch(`/api/v1/portfolio/performance?benchmark=${encodeURIComponent(benchmark)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (!cancelled) setPerformanceState({ request: performanceRequest, data }); })
      .catch(err => {
        console.error("Erreur calcul de la performance réelle :", err);
        if (!cancelled) setPerformanceState({ request: performanceRequest, data: null });
      });
    return () => { cancelled = true; };
  }, [performanceRequest]);
  const performanceData = performanceRequest ? performanceState.data : null;

  // 3 bis. Valeur historique des positions actuelles (quantités du jour × cours passés), vue continue
  const holdingsRequest = useMemo(() => {
    const held = portfolio
      .filter(a => !NON_LISTED_TICKERS.includes(a.ticker))
      .map(a => [a.ticker, getAssetTotalShares(a)])
      .filter(([, shares]) => shares > 0);
    return held.length > 0
      ? JSON.stringify({ held, benchmark: benchmarkTicker, years: historicalYears, refresh: dataRefreshTick })
      : null;
  }, [portfolio, benchmarkTicker, historicalYears, dataRefreshTick]);
  const [holdingsState, setHoldingsState] = useState({ request: null, data: null });

  useEffect(() => {
    if (!holdingsRequest) return undefined;
    let cancelled = false;
    const { benchmark, years } = JSON.parse(holdingsRequest);
    fetch(`/api/v1/portfolio/holdings-history?years=${years}&benchmark=${encodeURIComponent(benchmark)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (!cancelled) setHoldingsState({ request: holdingsRequest, data }); })
      .catch(err => {
        console.error("Erreur de la valeur historique des positions :", err);
        if (!cancelled) setHoldingsState({ request: holdingsRequest, data: null });
      });
    return () => { cancelled = true; };
  }, [holdingsRequest]);
  const holdingsHistory = holdingsRequest ? holdingsState.data : null;

  // 4. Exposition géographique (répartitions par pays stockées en base)
  const geoRequest = useMemo(() => {
    const tickers = [...new Set(portfolio.map(a => a.ticker).filter(Boolean))].sort();
    return tickers.length > 0 ? JSON.stringify(tickers) : null;
  }, [portfolio]);
  const [geoState, setGeoState] = useState({ request: null, data: {} });

  useEffect(() => {
    if (!geoRequest) return undefined;
    let cancelled = false;
    fetch('/api/v1/portfolio/geographic-exposure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: JSON.parse(geoRequest) })
    })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (!cancelled) setGeoState({ request: geoRequest, data: data?.assets || {} }); })
      .catch(err => {
        console.warn("Erreur récupération exposition géographique :", err);
        if (!cancelled) setGeoState({ request: geoRequest, data: {} });
      });
    return () => { cancelled = true; };
  }, [geoRequest]);
  const geoExposureData = geoRequest ? geoState.data : {};
  const isGeoLoading = Boolean(geoRequest) && geoState.request !== geoRequest;


  // Scraping & synchronisation des données réelles
  const [isScrapingInProgress, setIsScrapingInProgress] = useState(false);
  const [isAutoScrapingAsset, setIsAutoScrapingAsset] = useState(false);
  const [scrapingFeedback, setScrapingFeedback] = useState(null);

  const handleTriggerScraping = async () => {
    setIsScrapingInProgress(true);
    setScrapingFeedback("Scraping EOD en cours via le pipeline...");
    try {
      const res = await fetch('/api/v1/pipeline/daily-market', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setScrapingFeedback(data.message || "Cotations actualisées avec succès !");
        setDataRefreshTick(tick => tick + 1);
      } else {
        setScrapingFeedback("Erreur lors de l'exécution du pipeline.");
      }
    } catch (e) {
      console.error(e);
      setScrapingFeedback("Erreur réseau lors du scraping.");
    } finally {
      setIsScrapingInProgress(false);
      setTimeout(() => setScrapingFeedback(null), 5000);
    }
  };

  const handleAutoScrapeAndAdd = async (rawTicker) => {
    const cleanTicker = rawTicker.trim().toUpperCase();
    if (!cleanTicker) return;
    if (portfolio.some(p => p.ticker === cleanTicker)) {
      alert(`L'actif ${cleanTicker} est déjà présent dans votre portefeuille.`);
      return;
    }

    setIsAutoScrapingAsset(true);
    try {
      const res = await fetch(`/api/v1/assets/auto-scrape?ticker=${encodeURIComponent(cleanTicker)}`, {
        method: 'POST'
      });
      if (res.ok) {
        const assetData = await res.json();
        setAvailableAssets(prev => {
          if (prev.some(a => a.ticker === assetData.ticker)) return prev;
          return [
            {
              ticker: assetData.ticker,
              name: assetData.name,
              sector: assetData.sector,
              asset_class: assetData.asset_class
            },
            ...prev
          ];
        });

        const today = new Date().toISOString().split('T')[0];
        await postTransaction(assetData.ticker, { date: today, shares: 1, price: null, type: 'buy' });
        setSearchQuery('');
        setIsSearchFocused(false);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Échec du scraping direct pour ${cleanTicker} : ${err.detail || 'Actif introuvable sur les marchés'}`);
      }
    } catch (err) {
      console.error("Erreur auto-scrape :", err);
      alert(`Erreur de connexion lors du scraping de ${cleanTicker}`);
    } finally {
      setIsAutoScrapingAsset(false);
    }
  };

  const [popularTickers, setPopularTickers] = useState([
    'CW8.PA', 'WPEA.PA', 'PE500.PA', 'SPY', 'PUST.PA', 'QQQ',
    'XEON.DE', 'ETZ.PA', 'CAC.PA', 'PAEEM.PA', 'GLD', 'TLT', 'SCPI-EUR', 'PE-EUR'
  ]);

  useEffect(() => {
    fetch('/api/v1/etfs/popular?quick_only=true')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        if (Array.isArray(data) && data.length > 0) {
          const apiTickers = data.map(d => d.ticker).filter(Boolean);
          setPopularTickers([...apiTickers, 'SCPI-EUR', 'PE-EUR']);
        }
      })
      .catch(() => {});
  }, []);

  const filteredSuggestions = useMemo(() => {
    if (!searchQuery.trim()) {
      return isSearchFocused
        ? availableAssets.filter(asset => popularTickers.includes(asset.ticker) && !portfolio.some(p => p.ticker === asset.ticker))
        : [];
    }

    const query = searchQuery.trim().toLowerCase();
    const scoredCandidates = [];

    for (const asset of availableAssets) {
      if (portfolio.some(p => p.ticker === asset.ticker)) continue;

      const rawTicker = (asset.ticker || '').toLowerCase();
      const baseTicker = rawTicker.split('.')[0];
      const assetName = (asset.name || '').toLowerCase();
      const sector = (asset.sector || '').toLowerCase();

      let score = 0;

      // Tier 1 : Correspondance exacte du symbole ou du symbole racine (ex: SPY)
      if (rawTicker === query || baseTicker === query) {
        score = 1000;
      }
      // Tier 2 : Le symbole boursier commence par la requête (priorité aux tickers courts)
      else if (rawTicker.startsWith(query) || baseTicker.startsWith(query)) {
        score = 800 - rawTicker.length;
      }
      // Tier 3 : Le symbole boursier contient la requête
      else if (rawTicker.includes(query) || baseTicker.includes(query)) {
        score = 600 - rawTicker.length;
      }
      // Tier 4 : Le nom officiel commence par la requête ou contient un mot commençant par celle-ci
      else if (assetName.startsWith(query) || assetName.split(/[\s\-()]+/).some(w => w.startsWith(query))) {
        score = 400;
      }
      // Tier 5 : Le nom ou le secteur contient la chaîne recherchée
      else if (assetName.includes(query) || sector.includes(query)) {
        score = 200;
      }

      if (score > 0) {
        scoredCandidates.push({ asset, score });
      }
    }

    scoredCandidates.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return a.asset.ticker.localeCompare(b.asset.ticker);
    });

    return scoredCandidates.slice(0, 30).map(c => c.asset);
  }, [searchQuery, isSearchFocused, availableAssets, portfolio, popularTickers]);

  const postTransaction = async (ticker, tx) => {
    const res = await fetch('/api/v1/portfolio/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker,
        date: tx.date,
        type: tx.type,
        shares: tx.shares,
        // null : le serveur enregistre le cours de clôture du jour de l'opération
        price: tx.price === '' || tx.price === null || tx.price === undefined ? null : tx.price
      })
    });
    return applyPortfolioResponse(res);
  };

  const handleAddAsset = async (ticker) => {
    if (portfolio.some(asset => asset.ticker === ticker)) return;
    setSearchQuery('');
    setIsSearchFocused(false);
    const cat = getAssetCategory(ticker);
    if (cat.type === 'non_boursier') {
      // Pas de cours de marché : l'utilisateur saisit lui-même la valeur investie
      setSelectedAssetForTransactions(ticker);
      return;
    }
    const today = new Date().toISOString().split('T')[0];
    await postTransaction(ticker, { date: today, shares: 1, price: null, type: 'buy' });
  };

  const handleRemoveAsset = async (tickerToRemove) => {
    const res = await fetch(`/api/v1/portfolio/assets/${encodeURIComponent(tickerToRemove)}`, { method: 'DELETE' });
    await applyPortfolioResponse(res);
  };

  const handleAddTransaction = async (ticker, newTx) => {
    await postTransaction(ticker, newTx);
  };

  const handleRemoveTransaction = async (ticker, txId) => {
    const res = await fetch(`/api/v1/portfolio/transactions/${txId}`, { method: 'DELETE' });
    await applyPortfolioResponse(res);
  };

  const handleSimulate = useCallback(() => {
    if (!portfolio || portfolio.length === 0) return;

    const simAssets = portfolio
      .filter(a => !NON_LISTED_TICKERS.includes(a.ticker))
      .map(asset => ({
        ticker: asset.ticker,
        shares: getAssetTotalShares(asset)
      }))
      .filter(a => a.shares > 0); // une position entièrement vendue n'est plus simulée

    if (simAssets.length === 0) {
      alert("Veuillez inclure au moins un actif boursier (avec cours de marché) pour lancer le Stress Test Monte Carlo.");
      return;
    }

    const strategyPayload = { type: strategyType };
    if (strategyType === 'dca') {
      // Montant fixe en euros investi dans le même panier d'actifs à chaque échéance
      strategyPayload.params = { amount_eur: dcaAmount, frequency_days: dcaFrequency };
    }

    startSimulation({
      assets: simAssets,
      time_horizon_days: timeHorizon,
      num_paths: 10000,
      strategy: strategyPayload
    });
  }, [portfolio, strategyType, dcaAmount, dcaFrequency, timeHorizon, startSimulation]);



  const quantitativeData = result?.data || result;
  const histogramData = quantitativeData?.histogram;
  // VaR et ES sont renvoyées par le backend comme des PERTES (montant positif = perte en €)
  const var95 = quantitativeData?.value_at_risk_95;
  const cvar95 = quantitativeData?.expected_shortfall_95;
  // Sur l'axe du PnL, le seuil VaR se situe à -VaR ; l'axe étant catégoriel, on se cale sur la classe la plus proche
  const varPnlThreshold = var95 != null ? -var95 : null;
  const varBinPnl = varPnlThreshold != null && histogramData?.length
    ? histogramData.reduce((best, b) => (Math.abs(b.pnl - varPnlThreshold) < Math.abs(best.pnl - varPnlThreshold) ? b : best)).pnl
    : null;

  // Contexte dynamique et complet du portefeuille transmis au Copilot AI
  const portfolioContext = useMemo(() => {
    if (!portfolio || portfolio.length === 0) return null;

    const totalCostBasis = portfolio.reduce((acc, a) => acc + getAssetCostBasis(a), 0);
    const totalCurrentValue = portfolio.reduce((acc, a) => {
      const bd = (metricsData?.asset_breakdown || []).find(b => b.ticker === a.ticker);
      const shares = getAssetTotalShares(a);
      if (bd?.position_value !== undefined) return acc + bd.position_value;
      if (bd?.current_price !== undefined) return acc + (shares * bd.current_price);
      return acc + getAssetCostBasis(a);
    }, 0);

    let fnbVal = 0;
    let stockVal = 0;
    let unlistedVal = 0;

    const positions = portfolio.map(a => {
      const bd = (metricsData?.asset_breakdown || []).find(b => b.ticker === a.ticker);
      const shares = getAssetTotalShares(a);
      const pru = getAssetPru(a);
      const assetDetails = availableAssets.find(item => item.ticker === a.ticker);
      const cat = getAssetCategory(a.ticker, assetDetails);
      const currentPrice = bd?.current_price ?? (pru > 0 ? pru : null);
      const posVal = bd?.position_value ?? (shares > 0 && currentPrice != null ? shares * currentPrice : getAssetCostBasis(a));
      const weightPct = totalCurrentValue > 0 ? (posVal / totalCurrentValue) * 100 : 0;

      if (cat.type === 'fnb') fnbVal += posVal;
      else if (cat.type === 'action') stockVal += posVal;
      else unlistedVal += posVal;

      return {
        ticker: a.ticker,
        name: assetDetails ? assetDetails.name : a.ticker,
        shares,
        pru,
        current_price: currentPrice,
        value_eur: posVal,
        weight_pct: weightPct,
        category: cat.shortLabel || cat.type
      };
    });

    return {
      total_value: totalCurrentValue,
      cost_basis: totalCostBasis,
      positions,
      metrics: {
        sharpe_ratio: metricsData?.summary?.sharpe_ratio !== undefined ? metricsData.summary.sharpe_ratio.toFixed(2) : 'Calcul en cours',
        volatility_pct: metricsData?.summary?.annualized_volatility_pct !== undefined ? metricsData.summary.annualized_volatility_pct.toFixed(2) : (metricsData?.summary?.annualized_volatility !== undefined ? (metricsData.summary.annualized_volatility * 100).toFixed(2) : 'N/A'),
        annualized_return_pct: metricsData?.summary?.annualized_return_pct !== undefined ? metricsData.summary.annualized_return_pct.toFixed(2) : (metricsData?.summary?.annualized_return !== undefined ? (metricsData.summary.annualized_return * 100).toFixed(2) : 'N/A'),
        max_drawdown_pct: metricsData?.summary?.max_drawdown_pct !== undefined ? metricsData.summary.max_drawdown_pct.toFixed(2) : (metricsData?.summary?.max_drawdown !== undefined ? (metricsData.summary.max_drawdown * 100).toFixed(2) : 'N/A')
      },
      allocation: {
        fnb_pct: totalCurrentValue > 0 ? (fnbVal / totalCurrentValue) * 100 : 0,
        stocks_pct: totalCurrentValue > 0 ? (stockVal / totalCurrentValue) * 100 : 0,
        unlisted_pct: totalCurrentValue > 0 ? (unlistedVal / totalCurrentValue) * 100 : 0
      }
    };
  }, [portfolio, metricsData, availableAssets]);

  return (
    <div className="app-container">
      {/* Colonne Principale */}
      <div className="main-column">
        <header className="app-header">
          <div>
            <h1 className="main-title">Copilot AI</h1>
            <p className="main-subtitle">Suivi de portefeuille, analyse de risque et assistant documentaire sur ETF</p>
          </div>

          {/* Sélecteur d'onglets */}
          <nav className="tab-navigation">
            <div className="tab-buttons-group">
              <button
                className={`tab-btn ${activeTab === 'portfolio' ? 'active' : ''}`}
                onClick={() => setActiveTab('portfolio')}
              >
                <Icon name="portfolio" /> Portefeuille
              </button>
              <button
                className={`tab-btn ${activeTab === 'simulation' ? 'active' : ''}`}
                onClick={() => setActiveTab('simulation')}
              >
                <Icon name="risk" /> Risque et simulation
              </button>
            </div>

            <button
              type="button"
              className={`btn-header-copilot-trigger ${isCopilotOpen ? 'active' : ''}`}
              onClick={() => setIsCopilotOpen(!isCopilotOpen)}
              title="Ouvrir l'assistant documentaire"
            >
              <Icon name="assistant" />
              <span>Assistant</span>
            </button>
          </nav>
        </header>

        {/* Section 1 : Sélecteur et Recherche d'Actifs */}
        <div className="dashboard-panel search-only-panel">
          <div className="search-container">
            <div className="search-header-row">
              <label className="form-label">
                Ajouter un actif (ETF, actions du S&P 500 et du CAC 40) :
              </label>
              <div className="pipeline-sync-badge">
                <span className="pipeline-pulse-dot"></span>
                <span className="sync-text">{availableAssets.length} actifs disponibles</span>
                <button
                  type="button"
                  className={`btn-sync-refresh ${isScrapingInProgress ? 'loading' : ''}`}
                  onClick={handleTriggerScraping}
                  disabled={isScrapingInProgress}
                  title="Déclencher le scraping nocturne EOD et le recalcul quantitatif de covariance"
                >
                  <Icon name="refresh" size={14} /> {isScrapingInProgress ? 'Mise à jour en cours...' : 'Actualiser les cours'}
                </button>
                {scrapingFeedback && <span className="sync-feedback text-success">{scrapingFeedback}</span>}
              </div>
            </div>
            <input
              type="text"
              placeholder="Rechercher par symbole ou nom (ex: CW8, SPY, QQQ, TotalEnergies, Air Liquide)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setIsSearchFocused(true)}
              onClick={() => setIsSearchFocused(true)}
              onBlur={() => setTimeout(() => setIsSearchFocused(false), 200)}
              className="form-input"
            />

            {isSearchFocused && (
              <div className="search-dropdown">
                {!searchQuery && filteredSuggestions.length > 0 && (
                  <div className="dropdown-category">ETF & Actifs Populaires ({availableAssets.length} actifs réels disponibles)</div>
                )}
                {searchQuery.trim() && !availableAssets.some(a => a.ticker.toLowerCase() === searchQuery.trim().toLowerCase()) && (
                  <div
                    onMouseDown={(e) => {
                      e.preventDefault();
                      if (!isAutoScrapingAsset) handleAutoScrapeAndAdd(searchQuery);
                    }}
                    className="dropdown-item"
                    title="Télécharge la fiche et 3 ans de cours depuis Yahoo Finance, puis ajoute la ligne au portefeuille"
                  >
                    <div className="item-details">
                      <strong className="item-ticker">{searchQuery.trim().toUpperCase()}</strong>
                      <span className="item-name">
                        {isAutoScrapingAsset ? 'Import en cours depuis Yahoo Finance...' : 'Absent de la base : importer depuis Yahoo Finance'}
                      </span>
                    </div>
                    <span className="badge-add">+ Importer</span>
                  </div>
                )}
                {filteredSuggestions.length > 0 ? (
                  filteredSuggestions.map(asset => (
                    <div
                      key={asset.ticker}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        handleAddAsset(asset.ticker);
                      }}
                      className="dropdown-item"
                    >
                      <div className="item-details">
                        <strong className="item-ticker">{getDisplayTicker(asset.ticker)}</strong>
                        <span className="badge-exchange">{getExchangeName(asset.ticker)}</span>
                        <span className={`badge-google-type-small ${getAssetCategory(asset.ticker, asset).badgeClass}`}>
                          {getAssetCategory(asset.ticker, asset).shortLabel}
                        </span>
                        <span className="item-name">{asset.name}</span>
                      </div>
                      <span className="badge-add">+ Ajouter</span>
                    </div>
                  ))
                ) : (
                  <div className="dropdown-empty">
                    {searchQuery
                      ? `Aucun actif correspondant dans l'univers vérifié (${availableAssets.length} actifs disponibles).`
                      : "Chargement des actifs..."}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* --- ONGLET 1 : PORTEFEUILLE & ALLOCATION --- */}
        {activeTab === 'portfolio' && (() => {
          const totalCostBasis = portfolio.reduce((acc, a) => {
            return acc + getAssetCostBasis(a);
          }, 0);

          const totalCurrentValue = portfolio.reduce((acc, a) => {
            const bd = (metricsData?.asset_breakdown || []).find(b => b.ticker === a.ticker);
            const shares = getAssetTotalShares(a);
            if (bd?.position_value !== undefined) return acc + bd.position_value;
            if (bd?.current_price !== undefined) return acc + (shares * bd.current_price);
            return acc + getAssetCostBasis(a);
          }, 0);

          const totalGainLoss = totalCostBasis > 0 ? (totalCurrentValue - totalCostBasis) : 0;
          const totalGainLossPct = totalCostBasis > 0 ? (totalGainLoss / totalCostBasis) * 100 : 0;

          let fnbVal = 0;
          let stockVal = 0;
          let unlistedVal = 0;

          portfolio.forEach(a => {
            const bd = (metricsData?.asset_breakdown || []).find(b => b.ticker === a.ticker);
            const shares = getAssetTotalShares(a);
            const posVal = bd?.position_value ?? (bd?.current_price ? shares * bd.current_price : getAssetCostBasis(a));
            const assetDetails = availableAssets.find(item => item.ticker === a.ticker);
            const cat = getAssetCategory(a.ticker, assetDetails);

            if (cat.type === 'fnb') {
              fnbVal += posVal;
            } else if (cat.type === 'action') {
              stockVal += posVal;
            } else {
              unlistedVal += posVal;
            }
          });

          const geoBreakdown = calculateGeographicBreakdown(
            portfolio,
            metricsData?.asset_breakdown,
            totalCurrentValue,
            getAssetTotalShares,
            geoExposureData
          );

          const consolidatedBreakdown = portfolio.map(asset => {
            const bd = (metricsData?.asset_breakdown || []).find(b => b.ticker === asset.ticker);
            const shares = getAssetTotalShares(asset);
            const posVal = bd?.position_value ?? (bd?.current_price ? shares * bd.current_price : getAssetCostBasis(asset));
            const weightPct = totalCurrentValue > 0 ? (posVal / totalCurrentValue) * 100 : 0;
            return {
              ticker: asset.ticker,
              position_value: posVal,
              weight_pct: weightPct
            };
          });

          return (
            <div className="tab-content">
              {/* Grille de KPIs Financiers Épurée (Valorisation, Rendement CAGR, Sharpe, Volatilité) */}
              <MetricsCards
                summary={metricsData?.summary}
                performance={performanceData?.summary}
                totalCurrentValue={totalCurrentValue}
                totalCostBasis={totalCostBasis}
              />

              {/* Tableau des positions */}
              <div className="positions-card">
                <div className="chart-header-row">
                  <div>
                    <h4 className="chart-title">Positions</h4>
                  </div>
                  {isMetricsLoading && (
                    <span className="metrics-loading-indicator">Actualisation des cours...</span>
                  )}
                </div>

                <div className="table-wrapper">
                  <table className="positions-table">
                    <thead>
                      <tr>
                        <th>Actif</th>
                        <th className="center">Quantité</th>
                        <th className="right">Prix d'achat (PRU)</th>
                        <th className="right">Cours actuel</th>
                        <th className="right">Valeur totale</th>
                        <th className="right">Plus/Moins-value</th>
                        <th className="right">Poids</th>
                        <th className="center">Composition</th>
                        <th className="center" style={{ width: '48px' }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {portfolio.length === 0 ? (
                        <tr>
                          <td colSpan={9} className="center text-muted" style={{ padding: '2rem' }}>
                            {portfolioError || "Aucun actif dans le portefeuille. Utilisez la barre de recherche ci-dessus pour ajouter des ETFs ou actions."}
                          </td>
                        </tr>
                      ) : (
                        portfolio.map((asset, idx) => {
                          const assetDetails = availableAssets.find(a => a.ticker === asset.ticker);
                          const fullName = assetDetails ? assetDetails.name : asset.ticker;
                          const breakdown = (metricsData?.asset_breakdown || []).find(b => b.ticker === asset.ticker);

                          const shares = getAssetTotalShares(asset);
                          const costBasis = getAssetCostBasis(asset);
                          const pru = getAssetPru(asset);
                          const currentPrice = breakdown?.current_price;
                          const positionValue = breakdown?.position_value ?? (currentPrice ? shares * currentPrice : undefined);

                          const hasCostBasis = costBasis > 0;
                          const hasValuation = positionValue !== undefined;
                          const gainLoss = hasCostBasis && hasValuation ? (positionValue - costBasis) : null;
                          const gainLossPct = hasCostBasis && costBasis > 0 && gainLoss !== null ? (gainLoss / costBasis) * 100 : null;

                          return (
                            <tr key={asset.ticker}>
                              {/* 1. Actif & Marché */}
                              <td>
                                <div className="table-asset-cell">
                                  <div className="table-asset-header">
                                    <strong className="table-asset-ticker">{getDisplayTicker(asset.ticker)}</strong>
                                    <span className="badge-exchange">{getExchangeName(asset.ticker)}</span>
                                  </div>
                                  <span className="table-asset-name" title={fullName}>{fullName}</span>
                                </div>
                              </td>

                               {/* 2. Quantité (cliquable : ouvre la gestion des opérations) */}
                              <td className="center">
                                <button
                                  type="button"
                                  className="btn-quantity-cell"
                                  onClick={() => setSelectedAssetForTransactions(asset.ticker)}
                                  title="Gérer les opérations : ajouter un achat ou enregistrer une vente"
                                >
                                  <span className="quantity-num">{shares}</span>
                                  <span className="pencil-icon-wrapper" aria-hidden="true">
                                    <svg className="pencil-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path>
                                    </svg>
                                  </span>
                                </button>
                              </td>

                              {/* 3. Prix d'achat (PRU) moyen pondéré */}
                              <td className="right">
                                <span className="pru-val">
                                  {pru > 0
                                    ? `${pru.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                                    : '—'}
                                </span>
                              </td>

                              {/* 4. Cours actuel */}
                              <td className="right">
                                <span className="current-price-val">
                                  {currentPrice !== undefined
                                    ? `${currentPrice.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                                    : '—'}
                                </span>
                              </td>

                              {/* 5. Valeur totale */}
                              <td className="right">
                                <strong className="position-value-val">
                                  {positionValue !== undefined
                                    ? `${positionValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                                    : '—'}
                                </strong>
                              </td>

                              {/* 6. Plus/Moins-value (PnL latente) */}
                              <td className="right">
                                {gainLoss !== null ? (
                                  <div className="pnl-cell">
                                    <span className={`pnl-amount ${toneClass(gainLoss)}`}>
                                      {gainLoss >= 0 ? '+' : ''}
                                      {gainLoss.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                                    </span>
                                    <span className={`pnl-badge pnl-badge-${signTone(gainLoss)}`}>
                                      {gainLoss >= 0 ? '+' : ''}{gainLossPct.toFixed(2)}%
                                    </span>
                                  </div>
                                ) : (
                                  <span className="text-muted">—</span>
                                )}
                              </td>

                              {/* 7. Poids dans le portefeuille */}
                              <td className="right">
                                <span className="weight-val">
                                  {breakdown ? `${breakdown.weight_pct}%` : '—'}
                                </span>
                              </td>

                              {/* 8. Composition */}
                              <td className="center">
                                <button
                                  type="button"
                                  onClick={() => setSelectedLookThroughTicker(asset.ticker)}
                                  className="btn-table-lookthrough"
                                  title={`Composition de ${asset.ticker}`}
                                >
                                  <Icon name="search" size={14} /> Composition
                                </button>
                              </td>

                              {/* 9. Menu d'actions (enregistrer une opération / supprimer la ligne) */}
                              <td className="center">
                                <div className="table-kebab-wrapper">
                                  <button
                                    type="button"
                                    className={`btn-kebab-menu ${openMenuTicker === asset.ticker ? 'active' : ''}`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setOpenMenuTicker(openMenuTicker === asset.ticker ? null : asset.ticker);
                                    }}
                                    title="Options"
                                    aria-label="Options"
                                  >
                                    <svg className="kebab-svg" viewBox="0 0 24 24" fill="currentColor">
                                      <circle cx="12" cy="5" r="1.75" />
                                      <circle cx="12" cy="12" r="1.75" />
                                      <circle cx="12" cy="19" r="1.75" />
                                    </svg>
                                  </button>

                                  {openMenuTicker === asset.ticker && (
                                    <div className={`google-dropdown-menu ${idx >= portfolio.length - 2 && portfolio.length > 2 ? 'dropdown-upwards' : ''}`}>
                                      <button
                                        type="button"
                                        className="google-dropdown-item"
                                        onClick={() => {
                                          setOpenMenuTicker(null);
                                          setSelectedAssetForTransactions(asset.ticker);
                                        }}
                                      >
                                        <svg className="dropdown-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path>
                                          <circle cx="7" cy="7" r="1.5" fill="currentColor"></circle>
                                        </svg>
                                        <span>Enregistrer une opération</span>
                                      </button>
                                      <button
                                        type="button"
                                        className="google-dropdown-item google-dropdown-item-danger"
                                        onClick={() => {
                                          setOpenMenuTicker(null);
                                          handleRemoveAsset(asset.ticker);
                                        }}
                                      >
                                        <svg className="dropdown-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                          <polyline points="3 6 5 6 21 6"></polyline>
                                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                        </svg>
                                        <span>Supprimer</span>
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                    {portfolio.length > 0 && (
                      <tfoot>
                        <tr className="table-summary-row">
                          <td>
                            <strong>Total ({portfolio.length} positions)</strong>
                          </td>
                          <td className="center">
                            <strong>
                              {portfolio.reduce((acc, a) => acc + getAssetTotalShares(a), 0)}
                            </strong>
                          </td>
                          <td className="right">
                            <div className="summary-subtext">Coût d'achat global</div>
                            <strong>
                              {totalCostBasis > 0
                                ? `${totalCostBasis.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                                : '—'}
                            </strong>
                          </td>
                          <td></td>
                          <td className="right">
                            <div className="summary-subtext">Valorisation actuelle</div>
                            <strong className="summary-total-val">
                              {totalCurrentValue > 0
                                ? `${totalCurrentValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                                : '—'}
                            </strong>
                          </td>
                          <td className="right">
                            {totalCostBasis > 0 && totalCurrentValue > 0 ? (
                              <div className="pnl-cell">
                                <span className={`pnl-amount ${toneClass(totalGainLoss)}`}>
                                  {totalGainLoss >= 0 ? '+' : ''}
                                  {totalGainLoss.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                                </span>
                                <span className={`pnl-badge pnl-badge-${signTone(totalGainLoss)}`}>
                                  {totalGainLoss >= 0 ? '+' : ''}{totalGainLossPct.toFixed(2)}%
                                </span>
                              </div>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                          <td className="right">
                            <strong>100.0%</strong>
                          </td>
                          <td colSpan={2}></td>
                        </tr>
                      </tfoot>
                    )}
                  </table>
                </div>
              </div>

              {/* Section Graphiques : 3 Camemberts/Donuts (Allocation, Coté/Non Coté, Géographie) + Historique vs Benchmark */}
              <div className="allocation-triplet-grid">
                {/* Camembert 1 : Allocation par Actif */}
                <div className="chart-card allocation-card">
                  <PortfolioAllocationChart
                    assetBreakdown={consolidatedBreakdown}
                    totalInvestment={totalCurrentValue}
                  />
                </div>

                {/* Camembert 2 : typologie des actifs (ETF, actions, non coté) */}
                <div className="chart-card allocation-card">
                  <ListedAllocationChart
                    fnbAmount={fnbVal}
                    stockAmount={stockVal}
                    unlistedAmount={unlistedVal}
                    totalInvestment={totalCurrentValue}
                  />
                </div>

                {/* Camembert 3 : Expositions Géographiques */}
                <div className="chart-card allocation-card">
                  <GeographicAllocationChart
                    geographicData={geoBreakdown}
                    isLiveScraped={Object.keys(geoExposureData).length > 0}
                    isLoading={isGeoLoading}
                  />
                </div>
              </div>

              {/* Historique de valorisation et comparaison au benchmark */}
              <div className="history-chart-fullwidth">
                <PortfolioHistoryChart
                  timeSeries={performanceData?.time_series || []}
                  holdingsSeries={holdingsHistory?.time_series || []}
                  emptyMessage={performanceData?.message}
                  benchmarkTicker={benchmarkTicker}
                  currentPeriod={historicalYears}
                  onPeriodChange={(yr) => setHistoricalYears(yr)}
                  onBenchmarkChange={(ticker) => setBenchmarkTicker(ticker)}
                  comparisonBenchmarks={metricsData?.comparison_benchmarks || []}
                />
              </div>
            </div>
          );
        })()}

        {/* --- ONGLET 2 : STRESS TEST & MONTE CARLO --- */}
        {activeTab === 'simulation' && (
          <div className="tab-content">
            {/* Stress tests sur les positions */}
            <StressTestPositionsTable
              portfolio={portfolio}
              availableAssets={availableAssets}
              metricsData={metricsData}
              getAssetTotalShares={getAssetTotalShares}
              getAssetCostBasis={getAssetCostBasis}
              onOpenLookThrough={(ticker) => setSelectedLookThroughTicker(ticker)}
              cvar95={cvar95}
            />

            <div className="dashboard-panel">
              <h3 className="section-title">Simulation Monte Carlo</h3>

              <div className="simulation-controls-row">
                <div className="control-group">
                  <label className="params-label">Horizon (séances de bourse) :</label>
                  <select
                    value={timeHorizon}
                    onChange={(e) => setTimeHorizon(parseInt(e.target.value))}
                    className="form-select"
                  >
                    <option value={63}>3 Mois (63 jours)</option>
                    <option value={126}>6 Mois (126 jours)</option>
                    <option value={252}>1 An (252 jours)</option>
                    <option value={504}>2 Ans (504 jours)</option>
                    <option value={756}>3 Ans (756 jours)</option>
                  </select>
                </div>

                <div className="control-group">
                  <label className="params-label">Stratégie :</label>
                  <select
                    value={strategyType}
                    onChange={(e) => setStrategyType(e.target.value)}
                    className="form-select"
                  >
                    <option value="buy_and_hold">Achat-conservation</option>
                    <option value="dca">Versements programmés (DCA)</option>
                  </select>
                </div>

              </div>

              {strategyType === 'dca' && (
                <div className="strategy-params-box">
                  <div className="param-col">
                    <label className="param-label">Montant par versement (€) :</label>
                    <input type="number" min="1" value={dcaAmount} onChange={(e) => setDcaAmount(parseFloat(e.target.value) || 0)} className="param-input" />
                  </div>
                  <div className="param-col">
                    <label className="param-label">Périodicité (jours ouvrés) :</label>
                    <input type="number" value={dcaFrequency} onChange={(e) => setDcaFrequency(parseInt(e.target.value) || 0)} className="param-input" />
                  </div>
                </div>
              )}

              <button
                onClick={handleSimulate}
                disabled={status === 'loading'}
                className="btn-simulate"
              >
                {status === 'loading' ? 'Simulation en cours (10 000 trajectoires)...' : 'Lancer la simulation Monte Carlo (10 000 trajectoires)'}
              </button>
              {status === 'error' && (
                <p className="section-subtitle text-danger">Simulation interrompue : {simulationError || 'erreur inconnue'}</p>
              )}
            </div>

            {/* Résultats de simulation */}
            {status === 'success' && histogramData && (
              <div className="results-container">
                <div className="metrics-row">
                  <div className="metric-card border-var">
                    <h3 className="metric-title">Value at Risk 95 %</h3>
                    <p className="metric-value text-danger">{var95?.toFixed(2)} €</p>
                    <span className="metric-desc">Dans 95 % des scénarios, la perte à l'horizon simulé ne dépasse pas ce montant. Aucune tendance de marché supposée (dérive nulle).</span>
                  </div>
                  <div className="metric-card border-cvar">
                    <h3 className="metric-title">Expected Shortfall 95 %</h3>
                    <p className="metric-value text-dark-danger">{cvar95?.toFixed(2)} €</p>
                    <span className="metric-desc">Perte moyenne dans les 5 % de pires scénarios.</span>
                  </div>
                </div>

                {/* Histogramme de distribution */}
                <div className="chart-wrapper medium">
                  <h4 className="chart-header">Distribution du résultat à l'horizon et seuil de VaR 95 %</h4>
                  <ResponsiveContainer width="100%" height="88%">
                    <BarChart data={histogramData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="pnl" tickFormatter={(val) => `${Math.round(val).toLocaleString('fr-FR')} €`} minTickGap={30} />
                      <YAxis />
                      <Tooltip formatter={(value) => [`${value} scénarios`, 'Fréquence']} labelFormatter={(label) => `PnL : ${label} €`} />
                      <Bar dataKey="frequence">
                        {histogramData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={varPnlThreshold != null && entry.pnl <= varPnlThreshold ? COLORS.negative : COLORS.accentSoft} />
                        ))}
                      </Bar>
                      <ReferenceLine
                        x={varBinPnl}
                        stroke={COLORS.negative}
                        strokeWidth={2}
                        strokeDasharray="4 4"
                        label={{ position: 'top', value: 'Seuil VaR 95 %', fill: COLORS.negative, fontSize: 12, fontWeight: 600 }}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Trajectoires simulées */}
                {quantitativeData?.paths && quantitativeData.paths.length > 0 && (() => {
                  // Calcul d'un domaine Y serré basé sur p1/p99 pour un rendu visuel frappant
                  const pathsData = quantitativeData.paths;
                  const allP1 = pathsData.map(d => d.p1).filter(v => v != null);
                  const allP99 = pathsData.map(d => d.p99).filter(v => v != null);
                  const yMin = Math.min(...allP1);
                  const yMax = Math.max(...allP99);
                  const yRange = yMax - yMin;
                  const padding = yRange * 0.10;
                  const domainMin = Math.max(0, Math.floor((yMin - padding) / 500) * 500);
                  const domainMax = Math.ceil((yMax + padding) / 500) * 500;

                  return (
                    <div className="chart-wrapper large">
                      <h3 className="chart-header">
                        Trajectoires simulées
                        <span className="chart-subtitle">
                          Médiane, centile 99 (favorable) et centile 1 (défavorable)
                        </span>
                      </h3>
                      <ResponsiveContainer width="100%" height="88%">
                        <LineChart data={pathsData} margin={{ top: 10, right: 20, left: 10, bottom: 5 }}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} />
                          <XAxis dataKey="day" tickFormatter={(val) => `J+${val}`} />
                          <YAxis
                            domain={[domainMin, domainMax]}
                            tickFormatter={(val) => {
                              if (val >= 1000) return `${(val / 1000).toFixed(1)}k€`;
                              return `${Math.round(val)} €`;
                            }}
                            width={60}
                          />
                          <Tooltip content={(props) => <CustomSimulationTooltip {...props} />} />

                          {Object.keys(pathsData[0] || {}).filter(key => key.startsWith('path_')).map((key) => (
                            <Line key={key} type="monotone" dataKey={key} stroke="#d5dae1" strokeWidth={0.8} dot={false} isAnimationActive={false} opacity={0.25} style={{ pointerEvents: 'none' }} />
                          ))}

                          <Line type="monotone" dataKey="p99" name="Scénario favorable (centile 99)" stroke={COLORS.positive} strokeWidth={2.5} dot={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="median" name="Trajectoire médiane" stroke={COLORS.accent} strokeWidth={2.5} dot={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="p1" name="Scénario défavorable (centile 1)" stroke={COLORS.negative} strokeWidth={2.5} dot={false} isAnimationActive={false} />

                          <ReferenceLine y={quantitativeData.initial_investment} stroke={COLORS.muted} strokeWidth={1.2} strokeDasharray="3 3" label={{ position: 'left', value: 'Capital initial', fill: '#475569', fontSize: 10 }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Assistant Copilot IA : Bulle Flottante & Fenêtre Détachable */}
      <FloatingCopilot
        isOpen={isCopilotOpen}
        setIsOpen={setIsCopilotOpen}
        isExpanded={isCopilotExpanded}
        setIsExpanded={setIsCopilotExpanded}
        portfolioContext={portfolioContext}
      />

      {/* Fenêtre de composition (positions, pays, secteurs) */}
      {selectedLookThroughTicker && (
        <LookThroughModal
          ticker={selectedLookThroughTicker}
          onClose={() => setSelectedLookThroughTicker(null)}
        />
      )}

      {/* Fenêtre de gestion des opérations d'achat et de vente */}
      {selectedAssetForTransactions && (() => {
        const assetObj = portfolio.find(a => a.ticker === selectedAssetForTransactions);
        const assetDetails = availableAssets.find(a => a.ticker === selectedAssetForTransactions);
        const breakdown = (metricsData?.asset_breakdown || []).find(b => b.ticker === selectedAssetForTransactions);

        return (
          <TransactionModal
            ticker={selectedAssetForTransactions}
            assetName={assetDetails ? assetDetails.name : selectedAssetForTransactions}
            currentPrice={breakdown?.current_price}
            transactions={assetObj?.transactions || []}
            onAddTransaction={handleAddTransaction}
            onRemoveTransaction={handleRemoveTransaction}
            onClose={() => setSelectedAssetForTransactions(null)}
          />
        );
      })()}
    </div>
  );
}

export default App;