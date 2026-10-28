import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';

const GEO_COLORS = {
  'États-Unis': '#2563eb',             // Bleu roi
  'Europe / France': '#10b981',        // Vert émeraude
  'Asie & Pacifique': '#f59e0b',       // Ambre
  'International / Autres': '#8b5cf6', // Violet
  'Autres pays (non détaillés)': '#a78bfa',
  'Non ventilé': '#94a3b8'             // Gris : aucune répartition publiée (non coté, or, monétaire...)
};

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const item = payload[0].payload;
    return (
      <div className="allocation-tooltip">
        <strong style={{ color: item.color }}>{item.name}</strong>
        <div className="tooltip-line">
          Exposition : <span>{item.value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</span>
        </div>
        <div className="tooltip-line">
          Pondération : <span>{item.weightPct.toFixed(1)}%</span>
        </div>
      </div>
    );
  }
  return null;
};

export const GeographicAllocationChart = ({
  geographicData = [],
  isLiveScraped = false,
  isLoading = false
}) => {
  if (!geographicData || geographicData.length === 0) {
    return (
      <div className="empty-allocation-box">
        <p>{isLoading ? 'Chargement des répartitions par pays...' : "Aucune répartition géographique disponible."}</p>
      </div>
    );
  }

  const chartData = geographicData.map((item) => ({
    name: item.region,
    value: item.amount,
    weightPct: item.percentage,
    color: GEO_COLORS[item.region] || '#64748b'
  }));

  const topRegion = chartData[0];

  return (
    <div className="allocation-chart-container">
      <div className="chart-header-row">
        <div>
          <h4 className="chart-title">Exposition Géographique</h4>
          <span className="chart-micro-subtitle">
            {isLoading ? 'Actualisation...' : (isLiveScraped ? 'Répartitions par pays publiées (JustETF, Yahoo Finance)' : 'Répartition par pays des positions')}
          </span>
        </div>
        {topRegion && (
          <span className="total-badge">
            Top : {topRegion.name} ({topRegion.weightPct.toFixed(1)}%)
          </span>
        )}
      </div>

      <div style={{ width: '100%', height: 230 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
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
                <span style={{ color: '#334155', fontWeight: 600, fontSize: '12px' }}>
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
