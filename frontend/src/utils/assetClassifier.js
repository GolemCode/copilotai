/**
 * Module de classification automatique des actifs financiers (Taxonomie Google Finance)
 * 
 * Google Finance classe automatiquement chaque instrument :
 * - FNB : Fonds Négocié en Bourse (Exchange-Traded Fund / ETF)
 * - Action : Titre vif d'entreprise cotée
 * - Fonds Non Boursier : Actifs privés non cotés (SCPI, Private Equity, Liquidités/Livrets)
 * 
 * Priorité au type d'instrument scrapé (quote_type Yahoo, stocké en base) ; à défaut,
 * heuristiques sur la classe d'actif, le nom et une liste de tickers connus.
 */

// Tickers d'actions individuelles (Titres vifs cotés)
const KNOWN_STOCKS = new Set([
  'AIR.PA', 'MC.PA', 'TTE.PA', 'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'GOOG',
  'AMZN', 'META', 'TSLA', 'ASML.AS', 'BNP.PA', 'SAN.PA', 'OR.PA',
  'RMS.PA', 'AI.PA', 'SU.PA', 'EL.PA', 'V', 'MA', 'JPM', 'UNH',
  'COST', 'AMD', 'BRK-B', 'BRK.B', 'LLY'
]);

const KNOWN_FNBS = new Set([
  'CW8.PA', 'WPEA.PA', 'SPY', 'QQQ', 'PE500.PA', 'ESE.PA', 'PUST.PA', 'CAC.PA', 'XLK',
  'XLF', 'GLD', 'AGG', 'TLT', 'XEON.DE', 'C3M.PA', 'ETZ.PA', 'MEUD.PA', 'PAEEM.PA',
  'VWCE.DE', 'PABZ.PA', 'XDPE.PA', 'VOO', 'VTI', 'IVV', 'IWM', 'VEA', 'VWO', 'EEM', 'EFA'
]);

// Préfixes des actifs non cotés en bourse
const UNLISTED_IDENTIFIERS = ['SCPI', 'PE-', 'CASH', 'LIVRET', 'FONDS-EUROS'];

export const getAssetCategory = (ticker, assetDetails = null) => {
  if (!ticker) {
    return {
      type: 'fnb',
      label: 'FNB (Fonds Négocié en Bourse)',
      shortLabel: 'FNB',
      fullLabel: 'FNB • ETF',
      badgeClass: 'badge-google-fnb',
      is_listed: true,
      is_fnb: true
    };
  }

  const cleanTicker = ticker.toUpperCase();

  // 1. Détection Fonds Non Boursier (SCPI, Private Equity, Liquidités, Livret A)
  if (
    UNLISTED_IDENTIFIERS.some(id => cleanTicker.startsWith(id)) ||
    ['SCPI-EUR', 'PE-EUR', 'CASH-EUR'].includes(cleanTicker) ||
    assetDetails?.asset_class === 'Non Boursier' ||
    assetDetails?.sector?.toLowerCase().includes('non coté') ||
    assetDetails?.sector?.toLowerCase().includes('privé')
  ) {
    return {
      type: 'non_boursier',
      label: 'Fonds Non Boursier (Actifs Privés)',
      shortLabel: 'Non boursier',
      fullLabel: 'Fonds Non Boursier (Actifs Privés)',
      badgeClass: 'badge-google-unlisted',
      is_listed: false,
      is_fnb: false
    };
  }

  // 2. Type d'instrument scrapé (Yahoo Finance, stocké en base) : prioritaire sur les heuristiques
  const quoteType = (assetDetails?.quote_type || '').toUpperCase();
  if (quoteType === 'ETF' || quoteType === 'MUTUALFUND') {
    return {
      type: 'fnb',
      label: 'FNB (Fonds Négocié en Bourse)',
      shortLabel: 'FNB',
      fullLabel: 'FNB • ETF',
      badgeClass: 'badge-google-fnb',
      is_listed: true,
      is_fnb: true
    };
  }
  if (quoteType === 'EQUITY') {
    return {
      type: 'action',
      label: 'Action (Titre vif coté)',
      shortLabel: 'Action',
      fullLabel: 'Action (Titre vif)',
      badgeClass: 'badge-google-stock',
      is_listed: true,
      is_fnb: false
    };
  }

  // 3. Repli tant que la fiche n'est pas collectée : classe d'actif, nom, tickers connus
  const nameLower = (assetDetails?.name || '').toLowerCase();
  const isExplicitFnbClass = ['ETF', 'Obligations', 'Matières Premières', 'Commodity'].includes(assetDetails?.asset_class);
  const isKnownFnb = KNOWN_FNBS.has(cleanTicker);
  const hasEtfKeywords = nameLower.includes('etf') || 
                         nameLower.includes('ucits') || 
                         nameLower.includes('spdr') || 
                         nameLower.includes('ishares') || 
                         nameLower.includes('xtrackers') || 
                         nameLower.includes('trust') ||
                         nameLower.includes('invesco qqq') ||
                         nameLower.includes('index fund') ||
                         (nameLower.includes('amundi') && !nameLower.includes('action'));

  // Si c'est explicitement un FNB ou un ETF avéré
  if (isKnownFnb || (isExplicitFnbClass && !KNOWN_STOCKS.has(cleanTicker)) || (hasEtfKeywords && assetDetails?.asset_class !== 'Actions')) {
    return {
      type: 'fnb',
      label: 'FNB (Fonds Négocié en Bourse)',
      shortLabel: 'FNB',
      fullLabel: 'FNB • ETF',
      badgeClass: 'badge-google-fnb',
      is_listed: true,
      is_fnb: true
    };
  }

  // 4. Détection Action (Titre vif coté : CAC 40, S&P 500, Euronext, Wall Street)
  const isExplicitAction = assetDetails?.asset_class === 'Actions';
  const isKnownStock = KNOWN_STOCKS.has(cleanTicker);
  const isNotEtf = !hasEtfKeywords && !isKnownFnb;

  if (isExplicitAction || isKnownStock || isNotEtf) {
    return {
      type: 'action',
      label: 'Action (Titre vif coté)',
      shortLabel: 'Action',
      fullLabel: 'Action (Titre vif)',
      badgeClass: 'badge-google-stock',
      is_listed: true,
      is_fnb: false
    };
  }

  // 5. Repli par défaut sur FNB
  return {
    type: 'fnb',
    label: 'FNB (Fonds Négocié en Bourse)',
    shortLabel: 'FNB',
    fullLabel: 'FNB • ETF',
    badgeClass: 'badge-google-fnb',
    is_listed: true,
    is_fnb: true
  };
};
