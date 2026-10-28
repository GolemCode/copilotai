// Icônes SVG au trait (24 × 24, couleur héritée du texte) : remplacent les émojis de l'interface
const PATHS = {
  portfolio: ['M21 12A9 9 0 1 1 12 3v9z', 'M15 3.5A9 9 0 0 1 20.5 9H15z'],
  risk: ['M3 12h4l3-8 4 16 3-8h4'],
  assistant: ['M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z', 'M8 9h8', 'M8 13h5'],
  close: ['M18 6 6 18', 'M6 6l12 12'],
  search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z', 'M21 21l-5-5'],
  external: ['M14 4h6v6', 'M20 4 10 14', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  document: ['M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z', 'M14 3v5h5', 'M9 13h6', 'M9 17h6'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v5', 'M12 8h.01'],
  alert: ['M10.3 4 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4a2 2 0 0 0-3.4 0z', 'M12 9v4', 'M12 17h.01'],
  refresh: ['M20 11a8 8 0 0 0-14.6-4.5L4 8', 'M4 3v5h5', 'M4 13a8 8 0 0 0 14.6 4.5L20 16', 'M20 21v-5h-5'],
  user: ['M20 21a8 8 0 0 0-16 0', 'M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5l3 2'],
  chevronDown: ['M6 9l6 6 6-6'],
  chevronUp: ['M18 15l-6-6-6 6'],
  arrowRight: ['M5 12h14', 'M13 6l6 6-6 6']
};

export const Icon = ({ name, size = 16, className = '', title }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={`ui-icon ${className}`}
    role={title ? 'img' : undefined}
    aria-hidden={title ? undefined : true}
    aria-label={title}
  >
    {(PATHS[name] || []).map((d, i) => <path key={i} d={d} />)}
  </svg>
);
