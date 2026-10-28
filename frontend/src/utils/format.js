// Fonctions d'affichage partagées par les composants (tickers, dates et pourcentages en français)

export const getDisplayTicker = (ticker) => ticker.split('.')[0];

export const getExchangeName = (ticker) => {
  if (ticker.endsWith('.PA')) return 'Paris (EUR)';
  if (ticker.endsWith('.DE')) return 'Xetra (EUR)';
  if (ticker.endsWith('.L')) return 'Londres';
  if (ticker.endsWith('.AS')) return 'Amsterdam';
  if (ticker.endsWith('.MI')) return 'Milan';
  if (ticker.includes('.')) return ticker.split('.')[1];
  return 'US (USD)';
};

export const formatDate = (iso) => (iso ? new Date(iso).toLocaleDateString('fr-FR') : '');

// signed : préfixe "+" pour les valeurs positives (performances, plus-values)
export const formatPct = (value, signed = false) => {
  if (value === null || value === undefined) return '—';
  const sign = signed && value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)} %`;
};

// Tonalité d'un résultat : vert pour un gain, rouge pour une perte, neutre s'il s'arrondit à zéro
export const signTone = (value) => {
  if (value === null || value === undefined || Math.abs(value) < 0.005) return 'neutral';
  return value > 0 ? 'pos' : 'neg';
};
export const toneClass = (value) => ({ pos: 'text-success', neg: 'text-danger', neutral: '' })[signTone(value)];
