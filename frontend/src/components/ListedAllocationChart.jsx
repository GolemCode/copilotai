import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { COLORS, SERIES_COLORS } from '../utils/theme';

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const item = payload[0].payload;
    return (
      <div className="allocation-tooltip">
        <strong style={{ color: item.color }}>{item.name}</strong>
        <div className="tooltip-line">
          Montant : <span>{item.value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</span>
        </div>
        <div className="tooltip-line">
          Poids : <span>{item.weightPct.toFixed(1)}%</span>
        </div>
      </div>
    );
  }
  return null;
};

export const ListedAllocationChart = ({
  fnbAmount = 0,
  stockAmount = 0,
  unlistedAmount = 0,
  totalInvestment = 0
}) => {
  const total = (fnbAmount + stockAmount + unlistedAmount) || totalInvestment || 1;
  const fnbPct = (fnbAmount / total) * 100;
  const stockPct = (stockAmount / total) * 100;
  const unlistedPct = (unlistedAmount / total) * 100;

  const data = [
    {
      name: 'ETF (fonds indiciels cotés)',
      shortName: 'ETF',
      value: fnbAmount,
      weightPct: fnbPct,
      color: COLORS.accent
    },
    {
      name: 'Actions (Titres vifs)',
      shortName: 'Actions',
      value: stockAmount,
      weightPct: stockPct,
      color: COLORS.accentSoft
    },
    {
      name: 'Fonds Non Boursiers (Privés / SCPI)',
      shortName: 'Fonds Non Boursiers',
      value: unlistedAmount,
      weightPct: unlistedPct,
      color: SERIES_COLORS[3]
    }
  ].filter(d => d.value > 0);

  // Fallback si aucune donnée
  const displayData = data.length > 0 ? data : [
    { name: 'ETF', value: 100, weightPct: 100, color: COLORS.accent }
  ];

  return (
    <div className="allocation-chart-container">
      <div className="chart-header-row">
        <div>
          <h4 className="chart-title">Types d'actifs</h4>
          <span className="chart-micro-subtitle">ETF, actions et actifs non cotés</span>
        </div>
        <div className="allocation-tags-pill">
          {fnbAmount > 0 && (
            <span className="pill-item text-primary">{fnbPct.toFixed(1)} % ETF</span>
          )}
          {stockAmount > 0 && (
            <span className="pill-item text-purple">{stockPct.toFixed(1)}% Actions</span>
          )}
          {unlistedAmount > 0 && (
            <span className="pill-item text-success">{unlistedPct.toFixed(1)}% Non Boursier</span>
          )}
        </div>
      </div>

      <div style={{ width: '100%', height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              isAnimationActive={false}
              data={displayData}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
              paddingAngle={3}
              dataKey="value"
            >
              {displayData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} stroke={COLORS.surface} strokeWidth={1.5} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
            <Legend
              verticalAlign="bottom"
              formatter={(value, entry) => (
                <span style={{ color: COLORS.textSecondary, fontWeight: 500, fontSize: '12px' }}>
                  {entry.payload.shortName || value} ({entry.payload.weightPct.toFixed(1)}%)
                </span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

