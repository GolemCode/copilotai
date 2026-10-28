import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { COLORS, SERIES_COLORS } from '../utils/theme';

const PALETTE = SERIES_COLORS;

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
        <h4 className="chart-title">Répartition par ligne</h4>
        <span className="total-badge">
          {totalInvestment.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
        </span>
      </div>

      <div style={{ width: '100%', height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              isAnimationActive={false}
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={65}
              outerRadius={95}
              paddingAngle={3}
              dataKey="value"
            >
              {chartData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} stroke={COLORS.surface} strokeWidth={1.5} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
            <Legend
              verticalAlign="bottom"
              formatter={(value, entry) => (
                <span style={{ color: COLORS.textSecondary, fontWeight: 500, fontSize: '13px' }}>
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
