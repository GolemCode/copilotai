import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';

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
      name: 'FNB (Fonds Négociés en Bourse / ETF)',
      shortName: 'FNB (ETF)',
      value: fnbAmount,
      weightPct: fnbPct,
      color: '#2563eb'
    },
    {
      name: 'Actions (Titres vifs)',
      shortName: 'Actions',
      value: stockAmount,
      weightPct: stockPct,
      color: '#8b5cf6' // Violet
    },
    {
      name: 'Fonds Non Boursiers (Privés / SCPI)',
      shortName: 'Fonds Non Boursiers',
      value: unlistedAmount,
      weightPct: unlistedPct,
      color: '#10b981' // Vert émeraude
    }
  ].filter(d => d.value > 0);

  // Fallback si aucune donnée
  const displayData = data.length > 0 ? data : [
    { name: 'FNB (Fonds Négociés en Bourse)', value: 100, weightPct: 100, color: '#2563eb' }
  ];

  return (
    <div className="allocation-chart-container">
      <div className="chart-header-row">
        <div>
          <h4 className="chart-title">Typologie des Actifs</h4>
          <span className="chart-micro-subtitle">ETF, actions et actifs non cotés</span>
        </div>
        <div className="allocation-tags-pill">
          {fnbAmount > 0 && (
            <span className="pill-item text-primary">{fnbPct.toFixed(1)}% FNB</span>
          )}
          {stockAmount > 0 && (
            <span className="pill-item text-purple">{stockPct.toFixed(1)}% Actions</span>
          )}
          {unlistedAmount > 0 && (
            <span className="pill-item text-success">{unlistedPct.toFixed(1)}% Non Boursier</span>
          )}
        </div>
      </div>

      <div style={{ width: '100%', height: 230 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={displayData}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
              paddingAngle={3}
              dataKey="value"
            >
              {displayData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} stroke="#ffffff" strokeWidth={2} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
            <Legend
              verticalAlign="bottom"
              height={36}
              formatter={(value, entry) => (
                <span style={{ color: '#334155', fontWeight: 600, fontSize: '12px' }}>
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

