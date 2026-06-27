import { useState, useEffect } from 'react';
import { useRiskSimulation } from './hooks/useRiskSimulation';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer, Cell } from 'recharts';

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    const rawData = payload[0].payload;
    const mainMetrics = payload.filter(entry => ['p1', 'median', 'p99'].includes(entry.dataKey));

    return (
      <div style={{ backgroundColor: 'rgba(255, 255, 255, 0.95)', border: '1px solid #ccc', padding: '12px', borderRadius: '8px', boxShadow: '0 4px 6px rgba(0,0,0,0.1)' }}>
        <p style={{ margin: '0 0 10px 0', fontWeight: 'bold', color: '#495057', borderBottom: '1px solid #eee', paddingBottom: '5px' }}>
          Jour J+{label}
        </p>
        {mainMetrics.map((entry, index) => {
          const dataKey = entry.dataKey; // 'p1', 'median', ou 'p99'
          
          // Lecture directe des données absolues et investies transmises par le WebSocket
          const finalAbs = rawData[`abs_${dataKey}`];
          const finalInv = rawData[`inv_${dataKey}`];
          
          // Diagnostic visuel si le flux rencontre un autre goulot d'étranglement
          if (finalAbs === undefined || finalInv === undefined) {
            return (
              <div key={index} style={{ marginBottom: '8px', color: entry.color, fontSize: '13px' }}>
                <strong>{entry.name}</strong> : Variable indisponible dans le payload
              </div>
            );
          }

          const netPnl = finalAbs - finalInv;
          const pnlPct = finalInv > 0 ? (netPnl / finalInv) * 100 : 0;
          const sign = netPnl > 0 ? '+' : '';
          
          return (
            <div key={index} style={{ marginBottom: '8px' }}>
              <p style={{ margin: 0, color: entry.color, fontSize: '14px', fontWeight: 'bold' }}>
                {entry.name} : {finalAbs.toFixed(2)} €
              </p>
              <p style={{ margin: 0, fontSize: '12px', color: '#6c757d', paddingLeft: '8px' }}>
                 (Investi : {finalInv.toFixed(2)}€ | PnL : <span style={{color: netPnl >= 0 ? '#2b8a3e' : '#dc3545', fontWeight: 'bold'}}>{sign}{netPnl.toFixed(2)}€ / {sign}{pnlPct.toFixed(2)}%</span>)
              </p>
            </div>
          );
        })}
      </div>
    );
  }
  return null;
};

// Découpe le ticker (ex: "MC.PA" devient "MC")
const getDisplayTicker = (ticker) => ticker.split('.')[0];

// Déduit la place boursière depuis le suffixe Yahoo Finance
const getExchangeName = (ticker) => {
  if (ticker.endsWith('.PA')) return 'Paris';
  if (ticker.endsWith('.DE')) return 'Xetra (ALL)';
  if (ticker.endsWith('.L')) return 'Londres';
  if (ticker.endsWith('.AS')) return 'Amsterdam';
  if (ticker.endsWith('.MI')) return 'Milan';
  if (ticker.includes('.')) return ticker.split('.')[1]; // Règle de secours
  return 'US'; // Sans point, c'est par défaut NYSE ou NASDAQ
};

function App() {
  const { startSimulation, status, result } = useRiskSimulation();
  
  const [availableAssets, setAvailableAssets] = useState([]);
  const [portfolio, setPortfolio] = useState([]);

  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [timeHorizon, setTimeHorizon] = useState(252);

  // --- ÉTATS DE LA STRATÉGIE ---
  const [strategyType, setStrategyType] = useState('buy_and_hold');
  
  // Paramètres DCA
  const [dcaVolume, setDcaVolume] = useState(1);
  const [dcaFrequency, setDcaFrequency] = useState(30);
  
  // Paramètres Trigger (Achat/Vente sur seuil)
  const [triggerAction, setTriggerAction] = useState('sell'); // 'buy' ou 'sell'
  const [triggerPct, setTriggerPct] = useState(5);
  const [triggerWindow, setTriggerWindow] = useState(5); // en jours

  useEffect(() => {
    const fetchAssetsFromServer = async () => {
      try {
        const response = await fetch('http://localhost:8000/api/v1/assets');
        if (response.ok) {
          const data = await response.json();
          setAvailableAssets(data);
        } else {
          console.error("Erreur protocole : Réponse serveur incorrecte");
        }
      } catch (error) {
        console.error("Erreur réseau lors du chargement des actifs :", error);
      }
    };

    fetchAssetsFromServer();
  }, []);

  const popularTickers = ['SPY', 'QQQ', 'CW8.PA', 'GLD', 'AGG']; 

  const filteredSuggestions = searchQuery
    ? availableAssets
        .filter(asset => {
          const query = searchQuery.toLowerCase();
          const rawTicker = asset.ticker.toLowerCase();
          const cleanTicker = rawTicker.split('.')[0];
          const assetName = asset.name.toLowerCase();
          
          const isMatch = rawTicker.startsWith(query) || cleanTicker.startsWith(query) || assetName.includes(query);
          const notInPortfolio = !portfolio.some(p => p.ticker === asset.ticker);
          
          return isMatch && notInPortfolio;
        })
        .sort((a, b) => {
          const query = searchQuery.toLowerCase();
          
          // Nettoyage des données pour la comparaison
          const aCleanTicker = a.ticker.split('.')[0].toLowerCase();
          const bCleanTicker = b.ticker.split('.')[0].toLowerCase();
          const aName = a.name.toLowerCase();
          const bName = b.name.toLowerCase();

          // Fonction d'évaluation de la pertinence (Score le plus bas = meilleur)
          const getScore = (cleanTicker, name) => {
            if (cleanTicker === query) return 1; // Correspondance exacte du ticker
            if (cleanTicker.startsWith(query)) return 2; // Le ticker commence par la requête
            if (name.startsWith(query)) return 3; // Le nom de l'entreprise commence par la requête
            return 4; // La requête est contenue au milieu du nom ou du ticker
          };

          const scoreA = getScore(aCleanTicker, aName);
          const scoreB = getScore(bCleanTicker, bName);

          // Si les scores sont différents, le plus pertinent passe en premier
          if (scoreA !== scoreB) {
            return scoreA - scoreB;
          }
          
          // En cas d'égalité de score, on conserve le tri alphabétique naturel
          return a.ticker.localeCompare(b.ticker);
        })
        .slice(0, 50) 
    : isSearchFocused 
      ? availableAssets
          .filter(asset => 
            popularTickers.includes(asset.ticker) &&
            !portfolio.some(p => p.ticker === asset.ticker)
          )
      : [];
  
  const handleAddAsset = (ticker) => {
    if (portfolio.some(asset => asset.ticker === ticker)) {
      alert("Cet actif est déjà présent dans votre portefeuille.");
      return;
    }
    setPortfolio([...portfolio, { ticker: ticker, shares: 1 }]);
    setSearchQuery(''); 
    setIsSearchFocused(false);
  };

  const handleRemoveAsset = (tickerToRemove) => {
    setPortfolio(portfolio.filter(asset => asset.ticker !== tickerToRemove));
  };

  const handleUpdateShares = (ticker, newShares) => {
    setPortfolio(portfolio.map(asset => 
      asset.ticker === ticker ? { ...asset, shares: newShares } : asset
    ));
  };

  const handleSimulate = () => {
    if (portfolio.length === 0) {
      alert("Votre portefeuille doit contenir au moins un actif.");
      return;
    }

    // Calcul du nombre total d'actions initiales (pour déduire le prix unitaire)
    const totalShares = portfolio.reduce((acc, curr) => acc + (parseFloat(curr.shares) || 1), 0);

    // Construction du dictionnaire de stratégie avec le volume initial
    let strategyPayload = { 
      type: strategyType,
      initial_shares: totalShares // <--- INJECTION DU VOLUME DE DÉPART
    };
    
    if (strategyType === 'dca') {
      strategyPayload.params = { volume: dcaVolume, frequency_days: dcaFrequency };
    }

    startSimulation({
      assets: portfolio.map(asset => ({
        ticker: asset.ticker,
        shares: parseFloat(asset.shares) || 1
      })),
      time_horizon_days: timeHorizon,
      num_paths: 10000,
      strategy: strategyPayload // <--- Injection de la nouvelle donnée
    });
  };

  // Extraction des données scalaires
  const quantitativeData = result?.data || result;
  const histogramData = quantitativeData?.histogram;
  const var95 = quantitativeData?.value_at_risk_95;
  const cvar95 = quantitativeData?.expected_shortfall_95;
  
  // Transformation du format JSON "representative_paths" en tableau structuré pour Recharts
  const chartData = [];
  if (quantitativeData?.representative_paths) {
    const { worst, median, best } = quantitativeData.representative_paths;
    const daysCount = median.length;
    for (let i = 0; i < daysCount; i++) {
      chartData.push({
        day: i,
        worst: worst[i],
        median: median[i],
        best: best[i]
      });
    }
  }

  const dynamicInitialInvestment = chartData.length > 0 ? chartData[0].median : 0;

  const finalCapitalization = quantitativeData?.paths?.length > 0 
    ? quantitativeData.paths.at(-1).median 
    : 0;

  return (
    <div style={{ padding: '2rem', fontFamily: 'sans-serif', maxWidth: '1000px', margin: '0 auto' }}>
      <h1 style={{ borderBottom: '2px solid #eee', paddingBottom: '10px' }}>
        Dashboard Quantitatif - Analyse de Risque
      </h1>

      <div style={{ background: '#f8f9fa', padding: '1.5rem', borderRadius: '8px', marginBottom: '2rem' }}>
        
        {/* Conteneur relatif OBLIGATOIRE pour ancrer le menu déroulant */}
        <div style={{ marginBottom: '25px', position: 'relative' }}>
          <label style={{ fontWeight: 'bold', display: 'block', marginBottom: '8px' }}>
            Rechercher et ajouter un actif (Actions / ETF) :
          </label>
          <input
            type="text"
            placeholder="Rechercher un ticker (ex: AAPL, CW8)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onFocus={() => setIsSearchFocused(true)}
            onClick={() => setIsSearchFocused(true)}
            onBlur={() => setIsSearchFocused(false)}
            style={{ width: '100%', padding: '10px', fontSize: '16px', borderRadius: '4px', border: '1px solid #ccc' }}
          />
          
          {isSearchFocused && (
            <div style={{ 
              position: 'absolute', top: '100%', left: 0, right: 0, 
              backgroundColor: '#fff', border: '1px solid #ccc', borderRadius: '0 0 4px 4px', 
              zIndex: 9999, maxHeight: '250px', overflowY: 'auto', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' 
            }}>
              
              {!searchQuery && filteredSuggestions.length > 0 && (
                <div style={{ padding: '8px 15px', fontSize: '12px', color: '#868e96', backgroundColor: '#f8f9fa', fontWeight: 'bold', textTransform: 'uppercase' }}>
                  ETF Populaires
                </div>
              )}

              {filteredSuggestions.length > 0 ? (
                filteredSuggestions.map(asset => (
                  <div 
                    key={asset.ticker}
                    onMouseDown={(e) => {
                      e.preventDefault(); // Empêche la perte de focus qui détruirait le menu avant le clic
                      handleAddAsset(asset.ticker);
                    }}
                    style={{ padding: '10px 15px', cursor: 'pointer', borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                    onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#f1f3f5'}
                    onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#fff'}
                  >
                    <div style={{ display: 'flex', alignItems: 'center' }}>
                      <strong style={{ display: 'inline-block', width: '70px' }}>
                        {getDisplayTicker(asset.ticker)}
                      </strong>
                      <span style={{ fontSize: '11px', color: '#868e96', backgroundColor: '#f1f3f5', padding: '2px 6px', borderRadius: '4px', marginRight: '10px' }}>
                        {getExchangeName(asset.ticker)}
                      </span>
                      <span style={{ color: '#495057', fontSize: '14px' }}>
                        {asset.name}
                      </span>
                    </div>
                    <span style={{ fontSize: '12px', color: '#0b7285', backgroundColor: '#e3fafc', padding: '2px 6px', borderRadius: '10px' }}>Ajouter</span>
                  </div>
                ))
              ) : (
                <div style={{ padding: '15px', textAlign: 'center', color: '#868e96', fontStyle: 'italic' }}>
                  {availableAssets.length === 0 
                    ? "Chargement des actifs depuis l'API..." 
                    : "Aucun actif trouvé pour cette recherche."}
                </div>
              )}
            </div>
          )}
        </div>
        
        <h3 style={{ marginTop: 0 }}>Composition du Portefeuille</h3>
          {portfolio.map((asset) => {
            const assetDetails = availableAssets.find(a => a.ticker === asset.ticker);
            const fullName = assetDetails ? assetDetails.name : "";

            return (
              <div 
                key={asset.ticker} 
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px', marginBottom: '8px', border: '1px solid #e9ecef', borderRadius: '6px', backgroundColor: '#f8f9fa' }}
              >
                {/* Zone d'identification de l'actif */}
                <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                  <strong style={{ fontSize: '16px', color: '#212529', minWidth: '70px' }}>
                    {getDisplayTicker(asset.ticker)}
                  </strong>
                  
                  <span style={{ fontSize: '11px', color: '#868e96', backgroundColor: '#f1f3f5', padding: '2px 6px', borderRadius: '4px', marginRight: '12px' }}>
                    {getExchangeName(asset.ticker)}
                  </span>
                  
                  <span style={{ color: '#868e96', fontSize: '14px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '280px' }}>
                    {fullName}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
                  <div>
                    <label style={{ fontSize: '12px', color: '#495057', marginRight: '8px' }}>Parts</label>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={asset.shares}
                      onChange={(e) => {
                        const rawValue = e.target.value.replace(/[^0-9.,]/g, '').replace(',', '.');
                        handleUpdateShares(asset.ticker, rawValue);
                      }}
                      onBlur={(e) => {
                        let finalValue = parseFloat(e.target.value);
                        if (isNaN(finalValue) || finalValue <= 0) {
                          finalValue = 1;
                        }
                        handleUpdateShares(asset.ticker, finalValue);
                      }}
                      style={{ width: '80px', padding: '6px', borderRadius: '4px', border: '1px solid #ced4da', textAlign: 'right' }}
                    />
                  </div>
                  
                  <button
                    onClick={() => handleRemoveAsset(asset.ticker)}
                    style={{ padding: '6px 12px', backgroundColor: '#fff', color: '#fa5252', border: '1px solid #ffc9c9', borderRadius: '4px', cursor: 'pointer', fontSize: '13px', fontWeight: 'bold' }}
                    onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#ffe3e3'}
                    onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#fff'}
                  >
                    Retirer
                  </button>
                </div>
              </div>
            );
          })}

        <div style={{ marginTop: '20px', borderTop: '1px solid #ccc', paddingTop: '15px', display: 'flex', alignItems: 'center' }}>
          <label style={{ fontWeight: 'bold', marginRight: '15px', width: '250px' }}>
            Horizon Temporel (jours ouvrés) :
          </label>
          <input 
            type="number" 
            value={timeHorizon}
            onChange={(e) => setTimeHorizon(parseInt(e.target.value) || 0)}
            style={{ padding: '8px', width: '100px', borderRadius: '4px', border: '1px solid #ccc' }}
            disabled={status === 'loading'}
          />
        </div>
        
        {/* --- MODULE DE STRATÉGIE --- */}
        <div style={{ marginTop: '20px', borderTop: '1px solid #ccc', paddingTop: '20px', paddingBottom: '10px' }}>
          <h4 style={{ margin: '0 0 15px 0', color: '#495057' }}>Implémenter une stratégie</h4>
          
          <select 
            value={strategyType} 
            onChange={(e) => setStrategyType(e.target.value)}
            style={{ width: '100%', padding: '10px', marginBottom: '15px', borderRadius: '4px', border: '1px solid #ccc', backgroundColor: '#fff', fontSize: '15px' }}
            disabled={status === 'loading'}
          >
            <option value="buy_and_hold">Achat & Conservation (Passif)</option>
            <option value="dca">DCA (Investissement Programmé)</option>
            <option value="trigger">Momentum (Achat/Vente sur seuil)</option>
          </select>

          {/* Panneau DCA */}
          {strategyType === 'dca' && (
            <div style={{ display: 'flex', gap: '15px', backgroundColor: '#e9ecef', padding: '15px', borderRadius: '6px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold', marginBottom: '5px' }}>Volume d'actions ajouté</label>
                <input type="number" value={dcaVolume} onChange={(e) => setDcaVolume(parseInt(e.target.value) || 0)} style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ced4da' }} />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold', marginBottom: '5px' }}>Fréquence (Jours)</label>
                <input type="number" value={dcaFrequency} onChange={(e) => setDcaFrequency(parseInt(e.target.value) || 0)} style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ced4da' }} />
              </div>
            </div>
          )}

          {/* Panneau Trigger (Momentum) */}
          {strategyType === 'trigger' && (
            <div style={{ display: 'flex', gap: '15px', backgroundColor: '#e9ecef', padding: '15px', borderRadius: '6px', alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold', marginBottom: '5px' }}>Action</label>
                <select value={triggerAction} onChange={(e) => setTriggerAction(e.target.value)} style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ced4da' }}>
                  <option value="sell">Vendre</option>
                  <option value="buy">Renforcer</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold', marginBottom: '5px' }}>Si variation (%)</label>
                <input type="number" value={triggerPct} onChange={(e) => setTriggerPct(parseFloat(e.target.value) || 0)} style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ced4da' }} />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold', marginBottom: '5px' }}>Sur X Jours</label>
                <input type="number" value={triggerWindow} onChange={(e) => setTriggerWindow(parseInt(e.target.value) || 0)} style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ced4da' }} />
              </div>
            </div>
          )}
        </div>

        <button 
          onClick={handleSimulate} 
          disabled={status === 'loading'}
          style={{ 
            marginTop: '25px', width: '100%', padding: '15px', fontSize: '16px', 
            backgroundColor: '#000', color: 'white', border: 'none', borderRadius: '5px',
            cursor: status === 'loading' ? 'not-allowed' : 'pointer', fontWeight: 'bold'
          }}
        >
          {status === 'loading' ? 'Calcul stochastique en cours...' : 'Recalculer les trajectoires'}
        </button>
      </div>

      {status === 'success' && histogramData && (
        <div style={{ marginTop: '3rem' }}>
          <div style={{ display: 'flex', gap: '2rem', marginBottom: '2rem' }}>
            <div style={{ background: '#f8f9fa', padding: '1.5rem', borderRadius: '8px', flex: 1, borderLeft: '4px solid #dc3545' }}>
              <h3 style={{ margin: '0 0 10px 0', color: '#6c757d', fontSize: '15px' }}>
                Valorisation au 5e Centile (VaR Absolue)
              </h3>
              <p style={{ margin: 0, fontSize: '24px', fontWeight: 'bold', color: '#dc3545' }}>
                {var95?.toFixed(2)} €
              </p>
            </div>
            <div style={{ background: '#f8f9fa', padding: '1.5rem', borderRadius: '8px', flex: 1, borderLeft: '4px solid #850000' }}>
              <h3 style={{ margin: '0 0 10px 0', color: '#6c757d', fontSize: '15px' }}>
                Moyenne des pires 5% (Expected Shortfall)
              </h3>
              <p style={{ margin: 0, fontSize: '24px', fontWeight: 'bold', color: '#850000' }}>
                {cvar95?.toFixed(2)} €
              </p>
            </div>
          </div>

          <div style={{ height: '350px', minHeight: '350px', minWidth: 0, width: '100%', border: '1px solid #eee', borderRadius: '8px', padding: '1rem', marginBottom: '2rem' }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={histogramData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="pnl" tickFormatter={(val) => `${val}€`} minTickGap={30} />
                  <YAxis />
                  <Tooltip formatter={(value) => [`${value} scénarios`, 'Fréquence']} labelFormatter={(label) => `PnL : ${label} €`} />
                  <Bar dataKey="frequence">
                    {histogramData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.pnl <= var95 ? '#dc3545' : '#4dabf7'} />
                    ))}
                  </Bar>
                  <ReferenceLine 
                    x={var95} 
                    stroke="#dc3545" 
                    strokeWidth={2}
                    strokeDasharray="4 4"
                    label={{ 
                      position: 'top', 
                      value: 'VaR 95% (Seuil de perte)', 
                      fill: '#dc3545', 
                      fontSize: 12, 
                      fontWeight: 'bold' 
                    }} 
                  />
                </BarChart>
              </ResponsiveContainer>
          </div>

          {quantitativeData?.paths && quantitativeData.paths.length > 0 && (
            <>
              {/* Le Graphique du PnL Linéarisé */}
              <div style={{ height: '450px', minHeight: '450px', minWidth: 0, width: '100%', border: '1px solid #eee', borderRadius: '8px', padding: '1rem' }}>
                <h3 style={{ margin: '0 0 15px 0', color: '#6c757d', textAlign: 'center', fontSize: '16px' }}>
                  Analyse de la Valorisation (Courbes Linéarisées)
                  
                  <span style={{ display: 'block', fontSize: '13px', marginTop: '5px', color: '#868e96' }}>
                    Capital de base investi : {quantitativeData.initial_investment?.toFixed(2) || 0} €
                  </span>
                  
                  {/* AFFICHAGE DE LA CAPITALISATION ET DU PNL RELATIF (%) */}
                  {quantitativeData.metrics && (() => {
                    const { median_absolute, median_invested, median_pnl } = quantitativeData.metrics;
                    
                    // Calcul rigoureux du rendement (Performance pondérée)
                    const totalReturnPct = median_invested > 0 
                      ? ((median_absolute / median_invested) - 1) * 100 
                      : 0;

                    const isPositive = median_pnl >= 0;
                    const pnlColor = isPositive ? '#2b8a3e' : '#dc3545';
                    const sign = isPositive ? '+' : '';

                    return (
                      <span style={{ display: 'block', fontSize: '15px', marginTop: '10px', color: '#343a40', fontWeight: 'bold' }}>
                        Capitalisation Finale (Médiane) : {median_absolute.toFixed(2)} € 
                        <span style={{ fontSize: '14px', fontWeight: 'normal', color: '#6c757d', marginLeft: '6px' }}>
                          ( {median_invested.toFixed(2)} € investis 
                          <span style={{ color: pnlColor, fontWeight: 'bold', marginLeft: '4px' }}>
                            {sign}{median_pnl.toFixed(2)} €
                          </span> )
                        </span>
                        <span style={{ color: pnlColor, fontWeight: 'bold', marginLeft: '8px' }}>
                          ({totalReturnPct > 0 ? '+' : ''}{totalReturnPct.toFixed(2)}%)
                        </span>
                      </span>
                    );
                  })()}
                </h3>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={quantitativeData.paths} margin={{ top: 5, right: 20, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="day" tickFormatter={(val) => `J+${val}`} />
                    <YAxis tickFormatter={(val) => `${val > 0 ? '+' : ''}${(val / 1000).toFixed(1)}k€`} />
                    
                    <Tooltip content={(props) => <CustomTooltip {...props} baseCapital={quantitativeData.initial_investment || 0} />} />
                    
                    {Object.keys(quantitativeData.paths[0] || {}).filter(key => key.startsWith('path_')).map((key) => (
                        <Line key={key} type="monotone" dataKey={key} stroke="#ced4da" strokeWidth={1} dot={false} isAnimationActive={false} activeDot={false} opacity={0.4} style={{ pointerEvents: 'none' }} />
                    ))}

                    <Line type="monotone" dataKey="p99" name="Scénario Optimiste (1%)" stroke="#2b8a3e" strokeWidth={3} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="median" name="Trajectoire Médiane" stroke="#495057" strokeWidth={3} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="p1" name="Scénario de Crise (99%)" stroke="#dc3545" strokeWidth={3} dot={false} isAnimationActive={false} />

                    <ReferenceLine y={quantitativeData.initial_investment || 0} stroke="#000" strokeWidth={2} strokeDasharray="3 3" />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* L'historique des Transactions (Uniquement si la stratégie a déclenché des ordres) */}
              {quantitativeData.trade_history && quantitativeData.trade_history.length > 0 && (
                <div style={{ marginTop: '4rem', padding: '1.5rem', background: '#f8f9fa', borderRadius: '8px', borderLeft: '4px solid #4dabf7' }}>
                  <h3 style={{ margin: '0 0 15px 0', color: '#495057' }}>
                    Historique des Transactions (Tracé sur le Scénario de Référence)
                  </h3>
                  <div style={{ maxHeight: '250px', overflowY: 'auto', border: '1px solid #dee2e6', borderRadius: '6px', backgroundColor: '#fff' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px' }}>
                      <thead style={{ backgroundColor: '#e9ecef', position: 'sticky', top: 0, zIndex: 1 }}>
                        <tr>
                          <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #dee2e6' }}>Date (Jour)</th>
                          <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #dee2e6' }}>Type d'Opération</th>
                          <th style={{ padding: '12px', textAlign: 'center', borderBottom: '1px solid #dee2e6' }}>Volume</th>
                          <th style={{ padding: '12px', textAlign: 'right', borderBottom: '1px solid #dee2e6' }}>Prix d'achat</th>
                        </tr>
                      </thead>
                      <tbody>
                        {quantitativeData.trade_history.map((trade, idx) => (
                          <tr key={idx} style={{ borderBottom: '1px solid #f1f3f5' }}>
                            <td style={{ padding: '12px', color: '#868e96', fontWeight: 'bold' }}>J+{trade.day}</td>
                            <td style={{ padding: '12px', fontWeight: 'bold', color: trade.action.includes('profit') ? '#e67700' : '#2b8a3e' }}>
                              {trade.action}
                            </td>
                            <td style={{ padding: '12px', textAlign: 'center', color: '#495057', fontWeight: 'bold' }}>
                              x {trade.volume}
                            </td>
                            <td style={{ padding: '12px', textAlign: 'right', fontFamily: 'monospace', fontSize: '15px' }}>
                              {trade.amount.toFixed(2)} €
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default App;