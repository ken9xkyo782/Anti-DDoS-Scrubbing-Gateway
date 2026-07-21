import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { DROP_REASON_PALETTE, tooltipContentStyle, tooltipItemStyle, tooltipLabelStyle } from './chartTheme'
import styles from './dashboard.module.css'

interface DropReasonChartProps {
  dropByReason: Record<string, number>
}

export function DropReasonChart({ dropByReason }: DropReasonChartProps) {
  const data = Object.entries(dropByReason).map(([name, value]) => ({ name, value }))

  return (
    <section aria-label="Drops by reason" className={styles.section}>
      <h3 className={styles.title}>Drop reasons</h3>
      {data.length === 0 ? (
        <p className={styles.empty}>No dropped packets in this window.</p>
      ) : (
        <div className={styles.chart}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="80%" paddingAngle={2} label>
                {data.map((entry, index) => (
                  <Cell
                    key={entry.name}
                    fill={DROP_REASON_PALETTE[index % DROP_REASON_PALETTE.length]}
                    stroke="none"
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={tooltipContentStyle}
                itemStyle={tooltipItemStyle}
                labelStyle={tooltipLabelStyle}
              />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
