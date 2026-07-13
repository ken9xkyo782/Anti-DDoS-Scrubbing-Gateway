import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

interface CleanVsDropChartProps {
  cleanPackets: number
  droppedPackets: number
}

export function CleanVsDropChart({ cleanPackets, droppedPackets }: CleanVsDropChartProps) {
  return (
    <section aria-label="Clean versus dropped packets">
      <h3>Clean vs dropped packets</h3>
      <div style={{ height: 220, width: '100%' }}>
        <ResponsiveContainer>
          <BarChart data={[{ packets: 'Current window', clean: cleanPackets, dropped: droppedPackets }]}>
            <XAxis dataKey="packets" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="clean" fill="#198754" name="Clean" />
            <Bar dataKey="dropped" fill="#dc3545" name="Dropped" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
