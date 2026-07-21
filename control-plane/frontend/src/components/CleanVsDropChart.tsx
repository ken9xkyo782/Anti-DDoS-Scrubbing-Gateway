import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { CHART_COLORS, tooltipContentStyle, tooltipItemStyle, tooltipLabelStyle } from './chartTheme'
import styles from './dashboard.module.css'

interface CleanVsDropChartProps {
  cleanPackets: number
  droppedPackets: number
}

export function CleanVsDropChart({ cleanPackets, droppedPackets }: CleanVsDropChartProps) {
  return (
    <section aria-label="Clean versus dropped packets" className={styles.section}>
      <h3 className={styles.title}>Clean vs dropped packets</h3>
      <div className={styles.chart}>
        <ResponsiveContainer>
          <BarChart data={[{ packets: 'Current window', clean: cleanPackets, dropped: droppedPackets }]}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="packets" tickLine={false} axisLine={false} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={48} />
            <Tooltip
              cursor={{ fill: 'var(--accent-bg)' }}
              contentStyle={tooltipContentStyle}
              itemStyle={tooltipItemStyle}
              labelStyle={tooltipLabelStyle}
            />
            <Legend />
            <Bar dataKey="clean" fill={CHART_COLORS.clean} name="Clean" radius={[4, 4, 0, 0]} />
            <Bar dataKey="dropped" fill={CHART_COLORS.drop} name="Dropped" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
