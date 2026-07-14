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
    <section aria-label="Telemetry trend">
      <h3>Trend</h3>
      {data.length === 0 ? (
        <p>No telemetry history is available yet.</p>
      ) : (
        <div style={{ height: 240, width: '100%' }}>
          <ResponsiveContainer>
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="time" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Area
                type="monotone"
                dataKey="clean_pkts"
                name="Clean packets"
                stroke="#1a7f37"
                fill="#1a7f37"
              />
              <Area
                type="monotone"
                dataKey="drop_pkts"
                name="Dropped packets"
                stroke="#b42318"
                fill="#b42318"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
