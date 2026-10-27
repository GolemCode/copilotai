import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';

const PALETTE = [
  '#2563eb', // Bleu primaire
  '#10b981', // Émeraude
  '#f59e0b', // Ambre
  '#8b5cf6', // Violet
  '#ec4899', // Rose
  '#06b6d4', // Cyan
  '#f97316', // Orange
  '#14b8a6', // Turquoise
  '#6366f1', // Indigo
  '#84cc16'  // Lime
];

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="allocation-tooltip">
        <strong style={{ color: data.color }}>{data.name}</strong>
        <div className="tooltip-line">
          Valeur : <span>{data.value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</span>
        </div>
        <div className="tooltip-line">
          Poids : <span>{data.weightPct.toFixed(1)}%</span>
        </div>
      </div>
    );
  }
  return null;
};

export const PortfolioAllocationChart = ({ assetBreakdown = [], totalInvestment = 0 }) => {
  if (!assetBreakdown || assetBreakdown.length === 0) {
    return (
      <div className="empty-allocation-box">
        <p>Ajoutez des actifs pour visualiser l'allocation du portefeuille.</p>
      </div>
    );
  }

  const chartData = assetBreakdown.map((item, idx) => ({
    name: item.ticker,
    value: Number(item.position_value || (item.weight * totalInvestment) || 0),
    weightPct: Number(item.weight_pct || (item.weight * 100) || 0),
    color: PALETTE[idx % PALETTE.length]
  }));

  return (
    <div className="allocation-chart-container">
      <div className="chart-header-row">
        <h4 className="chart-title">Allocation d'Actifs</h4>
        <span className="total-badge">
          Total : {totalInvestment.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
        </span>
      </div>

      <div style={{ width: '100%', height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={65}
              outerRadius={95}
              paddingAngle={3}
              dataKey="value"
            >
              {chartData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} stroke="#ffffff" strokeWidth={2} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
            <Legend
              verticalAlign="bottom"
              height={36}
              formatter={(value, entry) => (
                <span style={{ color: '#334155', fontWeight: 600, fontSize: '13px' }}>
                  {value} ({entry.payload.weightPct.toFixed(1)}%)
                </span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
