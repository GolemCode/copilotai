import { useEffect, useState } from 'react';

export const LookThroughModal = ({ ticker, onClose }) => {
  // Dernière réponse reçue, associée au ticker demandé : l'état de chargement s'en déduit
  const [result, setResult] = useState({ ticker: null, data: null, error: null });

  // Chargement de la composition : ne dépend que du ticker (pas de la fonction onClose,
  // recréée à chaque rendu du parent, qui relançait la requête en boucle)
  useEffect(() => {
    if (!ticker) return undefined;
    let isMounted = true;

    fetch(`/api/v1/etf/${encodeURIComponent(ticker)}/look-through`)
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Erreur réseau : ${res.status}`);
        }
        return res.json();
      })
      .then((json) => {
        if (isMounted) setResult({ ticker, data: json, error: null });
      })
      .catch((err) => {
        if (isMounted) setResult({ ticker, data: null, error: err.message || 'Impossible de récupérer la composition.' });
      });

    return () => {
      isMounted = false;
    };
  }, [ticker]);

  // Fermeture par la touche Échap
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const loading = result.ticker !== ticker;
  const data = loading ? null : result.data;
  const error = loading ? null : result.error;

  if (!ticker) return null;

  return (
    <div className="lookthrough-backdrop" onClick={onClose}>
      <div className="lookthrough-modal" onClick={(e) => e.stopPropagation()}>
        {/* En-tête */}
        <div className="lookthrough-header">
          <div className="lookthrough-title-group">
            <div className="lookthrough-badge-row">
              <span className="lookthrough-ticker-badge">{ticker}</span>
              <span className="lookthrough-tag-mode">🔍 Mode Look-Through</span>
              {data?.is_etf === false && (
                <span className="lookthrough-tag-equity">Action Individuelle</span>
              )}
            </div>
            <h2 className="lookthrough-fund-name">
              {loading ? 'Chargement de la décomposition...' : (data?.name || ticker)}
            </h2>
            <div className="lookthrough-fund-meta">
              {data?.fund_family && <span className="meta-item">🏛️ {data.fund_family}</span>}
              {data?.category && <span className="meta-item">📁 {data.category}</span>}
              {data?.benchmark && <span className="meta-item">🎯 Indice : {data.benchmark}</span>}
            </div>
          </div>
          <button className="lookthrough-close-btn" onClick={onClose} title="Fermer (Échap)">
            ✕
          </button>
        </div>

        {/* Corps de la bulle info */}
        <div className="lookthrough-body">
          {loading && (
            <div className="lookthrough-loading-state">
              <div className="spinner"></div>
              <p>Extraction de la décomposition Look-Through depuis les sources officielles...</p>
            </div>
          )}

          {error && (
            <div className="lookthrough-error-box">
              ⚠️ Une erreur est survenue lors de la récupération des données : {error}
            </div>
          )}

          {!loading && !error && data && (
            <>
              {/* Liens officiels de vérification */}
              {(data.source_url || data.factsheet_url) && (
                <div className="lookthrough-links-toolbar">
                  {data.source_url && (
                    <a
                      href={data.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="source-verify-link"
                    >
                      🔗 Fiche Yahoo Finance ({ticker}) ↗
                    </a>
                  )}
                  {data.factsheet_url && (
                    <a
                      href={data.factsheet_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="source-verify-link factsheet"
                    >
                      {data.justetf_url ? "📄 Fiche JustETF (DICI & Inventaire) ↗" : "📄 Fiche KID & Factsheet Officielle ↗"}
                    </a>
                  )}
                </div>
              )}

              {/* Description */}
              {data.description && (
                <div className="lookthrough-desc-box">
                  <p>{data.description}</p>
                </div>
              )}

              {/* Note discrète de synthèse pour le Top (uniquement si plusieurs positions) */}
              {data.holdings && data.holdings.length > 1 && data.is_etf !== false && data.total_top_holdings_weight && (
                <div className="lookthrough-table-caption">
                  <span>Pondération cumulée du Top {data.holdings.length} :</span>
                  <strong className="caption-highlight">{data.total_top_holdings_weight}% de l'actif net</strong>
                </div>
              )}

              {/* Tableau de décomposition en % */}
              {data.holdings && data.holdings.length > 0 ? (
                <div className="lookthrough-table-wrapper">
                  <table className="lookthrough-table">
                    <thead>
                      <tr>
                        <th style={{ width: '45px' }}>#</th>
                        <th>Sous-jacent / Émetteur</th>
                        <th>Secteur / Notation</th>
                        <th className="right" style={{ width: '120px' }}>Poids (%)</th>
                        <th style={{ width: '140px' }}>Répartition</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.holdings.map((h, idx) => {
                        const weight = parseFloat(h.weight_pct) || 0;
                        const maxWeight = Math.max(...data.holdings.map(item => parseFloat(item.weight_pct) || 0), 10);
                        const barWidthPct = Math.min(100, Math.max(4, (weight / maxWeight) * 100));

                        return (
                          <tr key={h.symbol || idx}>
                            <td className="row-rank">{idx + 1}</td>
                            <td>
                              <div className="constituent-info">
                                <strong className="constituent-name">{h.name}</strong>
                                {h.symbol && <span className="constituent-symbol">{h.symbol}</span>}
                              </div>
                            </td>
                            <td>
                              <span className="constituent-sector">{h.sector || '—'}</span>
                            </td>
                            <td className="right">
                              <span className="constituent-weight">{weight.toFixed(2)}%</span>
                            </td>
                            <td>
                              <div className="weight-progress-track" title={`${weight.toFixed(2)}%`}>
                                <div
                                  className="weight-progress-bar"
                                  style={{ width: `${barWidthPct}%` }}
                                ></div>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="lookthrough-empty">
                  <div className="empty-icon">📊</div>
                  <p className="empty-message">
                    {data.empty_message || "Aucune décomposition détaillée des sous-jacents n'est disponible dans le flux direct pour cet actif."}
                  </p>
                  {data.factsheet_url && (
                    <a
                      href={data.factsheet_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="empty-action-link"
                    >
                      {data.justetf_url ? "Consulter la fiche détaillée et le DICI sur JustETF ↗" : "Consulter l'inventaire officiel sur Yahoo Finance ↗"}
                    </a>
                  )}
                </div>
              )}

              <div className="lookthrough-footer-note">
                {data.is_etf === false ? (
                  <>ℹ️ <strong>Détention directe :</strong> Ce titre vif est une participation directe au capital de l'entreprise cotée sans frais de gestion intermédiaire.</>
                ) : (data.holdings && data.holdings.length > 0) ? (
                  <>ℹ️ <strong>Transparence de portefeuille :</strong> Analyse par transparence des principales participations réelles pour mesurer la concentration sectorielle.</>
                ) : (
                  <>ℹ️ <strong>Inventaire de portefeuille :</strong> Les positions obligataires négociées de gré à gré sont détaillées dans le rapport semestriel de l'émetteur.</>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
