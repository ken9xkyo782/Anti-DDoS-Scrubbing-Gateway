import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

interface DropReasonChartProps {
  dropByReason: Record<string, number>
}

const COLORS = ['#dc3545', '#fd7e14', '#ffc107', '#6f42c1', '#0dcaf0']

export function DropReasonChart({ dropByReason }: DropReasonChartProps) {
  const data = Object.entries(dropByReason).map(([name, value]) => ({ name, value }))

  return (
    <section aria-label="Drops by reason">
      <h3>Drop reasons</h3>
      {data.length === 0 ? (
        <p>No dropped packets in this window.</p>
      ) : (
        <div style={{ height: 220, width: '100%' }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" label>
                {data.map((entry, index) => (
                  <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
