import { useState, useEffect } from 'react';
import { getAssetCategory } from '../utils/assetClassifier';
import { Icon } from './Icon';

export const TransactionModal = ({
  ticker,
  assetName,
  currentPrice,
  transactions = [],
  onAddTransaction,
  onRemoveTransaction,
  onClose
}) => {
  const todayStr = new Date().toISOString().split('T')[0];
  const [txMode, setTxMode] = useState('buy'); // 'buy' | 'sell'
  const [quantity, setQuantity] = useState(1);
  const [txDate, setTxDate] = useState(todayStr);
  // Prix vide : le serveur enregistre le cours de clôture du jour de l'opération (actif coté)
  const [txPrice, setTxPrice] = useState('');
  const isListed = getAssetCategory(ticker).type !== 'non_boursier';
  const [errorMsg, setErrorMsg] = useState('');

  // Fermeture par la touche Échap
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Calcul comptable rigoureux (ordre chronologique : Achat vs Vente, PRU et PnL réalisé)
  const accounting = (() => {
    const sortedTxs = [...transactions].sort((a, b) => new Date(a.date) - new Date(b.date));
    let currentQty = 0;
    let costBasis = 0;
    let pru = 0;
    let realizedPnl = 0;

    sortedTxs.forEach((t) => {
      const s = parseFloat(t.shares) || 0;
      const p = parseFloat(t.price) || 0;
      if (t.type === 'sell') {
        const sold = Math.min(currentQty, s);
        realizedPnl += sold * (p - pru);
        currentQty -= sold;
        costBasis = currentQty * pru;
      } else {
        const newQty = currentQty + s;
        costBasis += s * p;
        pru = newQty > 0 ? costBasis / newQty : 0;
        currentQty = newQty;
      }
    });

    const currentVal = currentPrice !== undefined ? currentQty * currentPrice : 0;
    const unrealizedPnl = costBasis > 0 && currentPrice !== undefined ? currentVal - costBasis : 0;
    const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : 0;

    return {
      currentQty,
      costBasis,
      pru,
      realizedPnl,
      currentVal,
      unrealizedPnl,
      unrealizedPnlPct
    };
  })();

  const handleStepQuantity = (delta) => {
    setQuantity((prev) => {
      const current = parseFloat(prev) || 0;
      const next = Math.max(1, current + delta);
      if (txMode === 'sell' && accounting.currentQty > 0) {
        return Math.min(accounting.currentQty, Number.isInteger(next) ? next : parseFloat(next.toFixed(2)));
      }
      return Number.isInteger(next) ? next : parseFloat(next.toFixed(2));
    });
  };

  const handleFormSubmit = (e) => {
    e.preventDefault();
    const qty = parseFloat(quantity);
    const prc = txPrice === '' ? null : parseFloat(txPrice);

    if (!qty || qty <= 0) {
      setErrorMsg('Veuillez entrer une quantité positive.');
      return;
    }
    if (prc === null && !isListed) {
      setErrorMsg('Actif non coté : veuillez saisir le prix unitaire.');
      return;
    }
    if (prc !== null && (isNaN(prc) || prc < 0)) {
      setErrorMsg('Veuillez entrer un prix unitaire valide.');
      return;
    }
    if (!txDate) {
      setErrorMsg('Veuillez sélectionner une date.');
      return;
    }

    if (txMode === 'sell') {
      if (accounting.currentQty <= 0) {
        setErrorMsg('Impossible d\'enregistrer une vente : vous ne détenez aucun titre de cet actif.');
        return;
      }
      if (qty > accounting.currentQty) {
        setErrorMsg(`Quantité insuffisante : vous détenez actuellement ${accounting.currentQty}.`);
        return;
      }
    }

    setErrorMsg('');
    onAddTransaction(ticker, {
      date: txDate,
      shares: qty,
      price: prc,
      type: txMode
    });

    // Réinitialiser le formulaire
    setQuantity(1);
    setTxDate(todayStr);
    setTxPrice('');
  };

  return (
    <div className="transaction-modal-backdrop" onClick={onClose}>
      <div className="transaction-modal-card" onClick={(e) => e.stopPropagation()}>
        {/* En-tête de la modale */}
        <div className="transaction-modal-header">
          <div className="modal-header-info">
            <div className="modal-ticker-row">
              <span className="modal-ticker-badge">{ticker}</span>
              {currentPrice !== undefined && (
                <span className="modal-price-pill">
                  Cours actuel : <strong>{currentPrice.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €</strong>
                </span>
              )}
            </div>
            <h3 className="modal-fund-title">{assetName || ticker}</h3>
          </div>
          <button className="transaction-close-btn" onClick={onClose} title="Fermer (Échap)" aria-label="Fermer">
            <Icon name="close" />
          </button>
        </div>

        {/* SECTION 1 : formulaire d'achat ou de vente */}
        <div className="transaction-form-section">
          {/* Boutons de bascule Achat / Vente */}
          <div className="google-tx-mode-toggle">
            <button
              type="button"
              className={`tx-mode-tab ${txMode === 'buy' ? 'active-buy' : ''}`}
              onClick={() => {
                setTxMode('buy');
                setErrorMsg('');
              }}
            >
              <span className="mode-tab-icon">+</span>
              Enregistrer un achat
            </button>
            <button
              type="button"
              className={`tx-mode-tab ${txMode === 'sell' ? 'active-sell' : ''}`}
              onClick={() => {
                setTxMode('sell');
                setErrorMsg('');
                if (accounting.currentQty > 0 && quantity > accounting.currentQty) {
                  setQuantity(accounting.currentQty);
                }
              }}
            >
              <span className="mode-tab-icon">–</span>
              Enregistrer une vente
            </button>
          </div>

          <form onSubmit={handleFormSubmit}>
            <div className="google-form-row">
              {/* Quantité avec stepper [-] [value] [+] */}
              <div className="form-field-group">
                <div style={{ display: 'flex', alignItems: 'center' }}>
                  <label className="google-field-label">
                    {txMode === 'buy' ? 'Quantité achetée' : 'Quantité vendue'}
                  </label>
                  {txMode === 'sell' && (
                    <span className="field-max-badge">
                      (Dispo : {accounting.currentQty})
                    </span>
                  )}
                </div>
                <div className="stepper-container">
                  <button
                    type="button"
                    className="stepper-btn stepper-btn-minus"
                    onClick={() => handleStepQuantity(-1)}
                    title="Diminuer"
                  >
                    –
                  </button>
                  <input
                    type="number"
                    min="0.01"
                    max={txMode === 'sell' ? accounting.currentQty : undefined}
                    step="any"
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                    className="stepper-input"
                    required
                  />
                  <button
                    type="button"
                    className="stepper-btn stepper-btn-plus"
                    onClick={() => handleStepQuantity(1)}
                    title="Augmenter"
                  >
                    +
                  </button>
                </div>
              </div>

              {/* Date */}
              <div className="form-field-group">
                <label className="google-field-label">
                  {txMode === 'buy' ? 'Date d\'achat' : 'Date de vente'}
                </label>
                <div className="date-picker-wrapper">
                  <input
                    type="date"
                    value={txDate}
                    onChange={(e) => setTxDate(e.target.value)}
                    className="google-date-input"
                    required
                  />
                </div>
              </div>

              {/* Prix */}
              <div className="form-field-group">
                <label className="google-field-label">
                  {txMode === 'buy' ? 'Prix d\'achat (€)' : 'Prix de vente (€)'}
                </label>
                <div className="price-input-wrapper">
                  <input
                    type="number"
                    step="0.001"
                    min="0"
                    placeholder={isListed ? 'Vide = cours de clôture du jour' : '0.00'}
                    value={txPrice}
                    onChange={(e) => setTxPrice(e.target.value)}
                    className="google-price-input"
                    required={!isListed}
                  />
                </div>
              </div>
            </div>

            {errorMsg && <div className="form-error-text">{errorMsg}</div>}

            {/* Boutons d'action */}
            <div className="google-actions-row">
              <button
                type="button"
                className="btn-google-cancel"
                onClick={onClose}
              >
                Annuler
              </button>
              <button
                type="submit"
                className={`btn-google-save ${txMode === 'sell' ? 'btn-google-sell' : ''}`}
              >
                {txMode === 'buy' ? 'Enregistrer l\'achat' : 'Enregistrer la vente'}
              </button>
            </div>
          </form>
        </div>

        {/* SECTION 2 : historique des opérations */}
        <div className="transactions-history-section">
          <div className="history-header-row">
            <h4 className="transaction-section-title">
              Historique des opérations ({transactions.length})
            </h4>
            <span className="history-subtitle">
              Retirez une opération pour ajuster vos positions
            </span>
          </div>

          {transactions.length === 0 ? (
            <div className="empty-history-notice">
              Aucune opération enregistrée pour cet actif. Utilisez le formulaire ci-dessus pour enregistrer un premier achat.
            </div>
          ) : (
            <div className="history-table-wrapper">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th className="right">Quantité</th>
                    <th className="right">Prix unitaire</th>
                    <th className="right">Montant</th>
                    <th className="center">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((tx) => {
                    const txShares = parseFloat(tx.shares) || 0;
                    const txPriceVal = parseFloat(tx.price) || 0;
                    const txTotal = txShares * txPriceVal;
                    const isSell = tx.type === 'sell';

                    return (
                      <tr key={tx.id}>
                        <td>
                          <span className="tx-date">
                            {new Date(tx.date).toLocaleDateString('fr-FR', {
                              day: '2-digit',
                              month: '2-digit',
                              year: 'numeric'
                            })}
                          </span>
                        </td>
                        <td>
                          <span className={`tx-type-badge ${isSell ? 'tx-type-sell' : 'tx-type-buy'}`}>
                            {isSell ? 'Vente' : 'Achat'}
                          </span>
                        </td>
                        <td className="right">
                          <strong className={`tx-shares ${isSell ? 'tx-shares-sell' : 'tx-shares-buy'}`}>
                            {isSell ? `-${txShares}` : `+${txShares}`}
                          </strong>
                        </td>
                        <td className="right">
                          <span
                            className="tx-price"
                            title={tx.price_source === 'market_close' ? 'Cours de clôture du jour de l\'opération (converti en EUR)' : 'Prix saisi'}
                          >
                            {txPriceVal.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €
                            {tx.price_source === 'market_close' && ' (clôture)'}
                          </span>
                        </td>
                        <td className="right">
                          <strong className="tx-total">
                            {txTotal.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €
                          </strong>
                        </td>
                        <td className="center">
                          {/* Suppression d'une opération */}
                          <button
                            type="button"
                            className="btn-action-icon btn-action-trash"
                            onClick={() => onRemoveTransaction(ticker, tx.id)}
                            title="Supprimer cette opération"
                          >
                            <svg className="action-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="3 6 5 6 21 6"></polyline>
                              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                              <line x1="10" y1="11" x2="10" y2="17"></line>
                              <line x1="14" y1="11" x2="14" y2="17"></line>
                            </svg>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Synthèse de la position consolidée (sans unité 'parts') */}
          {transactions.length > 0 && (
            <div className="position-recap-box">
              <div className="recap-item">
                <span className="recap-label">Quantité totale :</span>
                <strong className="recap-val">{accounting.currentQty}</strong>
              </div>
              <div className="recap-item">
                <span className="recap-label">PRU pondéré :</span>
                <strong className="recap-val">
                  {accounting.pru.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                </strong>
              </div>
              <div className="recap-item">
                <span className="recap-label">Montant investi :</span>
                <strong className="recap-val">
                  {accounting.costBasis.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €
                </strong>
              </div>
              {accounting.realizedPnl !== 0 && (
                <div className="recap-item">
                  <span className="recap-label">Plus-value réalisée :</span>
                  <strong className={`recap-val ${accounting.realizedPnl >= 0 ? 'text-success' : 'text-danger'}`}>
                    {accounting.realizedPnl >= 0 ? '+' : ''}{accounting.realizedPnl.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €
                  </strong>
                </div>
              )}
              {currentPrice !== undefined && (
                <div className="recap-item">
                  <span className="recap-label">Plus/Moins-value latente :</span>
                  <strong className={`recap-val ${accounting.unrealizedPnl >= 0 ? 'text-success' : 'text-danger'}`}>
                    {accounting.unrealizedPnl >= 0 ? '+' : ''}{accounting.unrealizedPnl.toLocaleString('fr-FR', { minimumFractionDigits: 2 })} € ({accounting.unrealizedPnl >= 0 ? '+' : ''}{accounting.unrealizedPnlPct.toFixed(2)}%)
                  </strong>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

