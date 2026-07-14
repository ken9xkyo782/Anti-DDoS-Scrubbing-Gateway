import type { TopPort, TopSource } from '../hooks/useServiceTelemetry'

interface TopTalkersPanelProps {
  topDstPorts: TopPort[]
  topSrc: TopSource[]
}

export function TopTalkersPanel({ topDstPorts, topSrc }: TopTalkersPanelProps) {
  return (
    <section aria-labelledby="top-talkers-heading">
      <h3 id="top-talkers-heading">Top talkers</h3>
      <p>Sampled from dropped-packet events — approximate, not exact counts.</p>
      <div>
        <h4>Top destination ports</h4>
        {topDstPorts.length === 0 ? (
          <p>No sampled destination ports in this window.</p>
        ) : (
          <ol>
            {topDstPorts.map((entry) => (
              <li key={entry.port}>
                Port {entry.port}: {entry.count.toLocaleString()}
              </li>
            ))}
          </ol>
        )}
      </div>
      <div>
        <h4>Top source addresses</h4>
        <p>Source IPs are sampled and not anonymized (pilot posture).</p>
        {topSrc.length === 0 ? (
          <p>No sampled source addresses in this window.</p>
        ) : (
          <ol>
            {topSrc.map((entry) => (
              <li key={entry.ip}>
                {entry.ip}: {entry.count.toLocaleString()}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}
