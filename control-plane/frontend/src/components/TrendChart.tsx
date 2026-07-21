import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { CHART_COLORS, tooltipContentStyle, tooltipItemStyle, tooltipLabelStyle } from './chartTheme'
import styles from './dashboard.module.css'

export interface TrendPoint {
  window_start: string
  clean_pkts: number
  drop_pkts: number
}

interface TrendChartProps {
  windows: TrendPoint[]
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString()
}

export function TrendChart({ windows }: TrendChartProps) {
  const data = windows.map((window) => ({ ...window, time: timeLabel(window.window_start) }))

  return (
    <section aria-label="Telemetry trend" className={styles.section}>
      <h3 className={styles.title}>Trend</h3>
      {data.length === 0 ? (
        <p className={styles.empty}>No telemetry history is available yet.</p>
      ) : (
        <div className={styles.chartTall}>
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="trend-clean" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART_COLORS.clean} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={CHART_COLORS.clean} stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="trend-drop" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART_COLORS.drop} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={CHART_COLORS.drop} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="time" tickLine={false} axisLine={false} minTickGap={24} />
              <YAxis tickLine={false} axisLine={false} width={48} />
              <Tooltip
                contentStyle={tooltipContentStyle}
                itemStyle={tooltipItemStyle}
                labelStyle={tooltipLabelStyle}
              />
              <Legend />
              <Area
                type="monotone"
                dataKey="clean_pkts"
                name="Clean packets"
                stroke={CHART_COLORS.clean}
                strokeWidth={2}
                fill="url(#trend-clean)"
              />
              <Area
                type="monotone"
                dataKey="drop_pkts"
                name="Dropped packets"
                stroke={CHART_COLORS.drop}
                strokeWidth={2}
                fill="url(#trend-drop)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
