import type { ReactNode } from 'react'
import { Badge, Card } from '../../../ui'

type CoverageBadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info'

interface CoverageItem {
  id: string
  title: string
  description: string
  badge: { label: string; variant: CoverageBadgeVariant }
  color: string
  colorBg: string
  icon: ReactNode
}

const iconProps = {
  width: 20,
  height: 20,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

/**
 * Static informational summary of every L3/L4 attack class the XDP/eBPF data
 * plane filters. This is documentation of the compiled pipeline (see
 * data-plane/src/*.h), not live config. It is safe to show to any authenticated
 * role because it carries no tenant-specific or tunable data.
 */
const PROTECTION_COVERAGE: CoverageItem[] = [
  {
    id: 'volumetric',
    title: 'Volumetric floods & scans',
    description:
      "Default-deny allow-rules and per-service token buckets meter UDP, ICMP and TCP SYN floods. Traffic to undeclared ports or over a rule's rate is dropped.",
    badge: { label: 'Policy-enforced', variant: 'default' },
    color: 'var(--color-info)',
    colorBg: 'var(--color-info-bg)',
    icon: <svg {...iconProps}><path d="M2 12h3l2.5 7 4-16 3 12 2-5 2.5 2H22" /></svg>,
  },
  {
    id: 'amplification',
    title: 'UDP reflection & amplification',
    description:
      'Well-known reflector source ports (DNS, NTP, SSDP, memcached…) are compiled into the fast path and dropped on sight.',
    badge: { label: 'Always on', variant: 'success' },
    color: 'var(--accent)',
    colorBg: 'var(--accent-bg)',
    icon: <svg {...iconProps}><path d="M13 2L4.5 13H11l-1 9 8.5-11H12l1-9z" /></svg>,
  },
  {
    id: 'bogon',
    title: 'Spoofed & bogon sources',
    description:
      'Sources in martian, private or reserved ranges — private, CGNAT, loopback, link-local, multicast — are dropped before any map lookup.',
    badge: { label: 'Always on', variant: 'success' },
    color: 'var(--color-warning)',
    colorBg: 'var(--color-warning-bg)',
    icon: (
      <svg {...iconProps}>
        <path d="M9 3h6l1 4c2 1 3 3 3 6v7l-2.5-1.5L14 21l-2-1.5L10 21l-2.5-1.5L5 20v-7c0-3 1-5 3-6l1-4z" />
        <circle cx="9.5" cy="12" r="1" />
        <circle cx="14.5" cy="12" r="1" />
      </svg>
    ),
  },
  {
    id: 'blacklist',
    title: 'Threat-intelligence blacklist',
    description:
      'Known-bad sources from global and per-service blacklists are matched through a bloom filter, confirmed by an LPM trie, and dropped.',
    badge: { label: 'Feed-driven', variant: 'info' },
    color: 'var(--color-danger)',
    colorBg: 'var(--color-danger-bg)',
    icon: (
      <svg {...iconProps}>
        <circle cx="12" cy="12" r="9" />
        <path d="M5.6 5.6l12.8 12.8" />
      </svg>
    ),
  },
  {
    id: 'malformed',
    title: 'Malformed & protocol evasion',
    description:
      'IPv6, IP fragments, malformed IPv4 and unsupported EtherTypes are rejected at parse time, before the policy engine runs.',
    badge: { label: 'Fail-fast', variant: 'default' },
    color: 'var(--text-muted)',
    colorBg: 'var(--border)',
    icon: (
      <svg {...iconProps}>
        <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" />
        <path d="M9 9l6 6M15 9l-6 6" />
      </svg>
    ),
  },
  {
    id: 'fairness',
    title: 'Capacity & fairness guards',
    description:
      "Ingress cost caps, service ceilings, node headroom and VIP ceilings shed excess load so one service under attack can't starve its neighbors.",
    badge: { label: 'Per-service', variant: 'default' },
    color: 'var(--color-ok)',
    colorBg: 'var(--color-ok-bg)',
    icon: (
      <svg {...iconProps}>
        <path d="M12 13a9 9 0 0 1 9 5H3a9 9 0 0 1 9-5z" />
        <path d="M12 13V7" />
        <path d="M16 9l-4 4" />
      </svg>
    ),
  },
]

/**
 * Read-only card grid describing the attack classes the data plane filters.
 * Shared by the admin DDoS Protection page and the tenant-facing overview.
 */
export function ProtectionCoverage() {
  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
          Protection coverage
        </h2>
        <p style={{ margin: 'var(--space-1) 0 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
          Attack classes the XDP/eBPF data plane filters today.
        </p>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(min(320px, 100%), 1fr))',
          gap: 'var(--space-3)',
        }}
      >
        {PROTECTION_COVERAGE.map((item) => (
          <Card
            key={item.id}
            style={{
              padding: 'var(--space-4)',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-3)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
              <div
                style={{
                  width: 40,
                  height: 40,
                  flex: 'none',
                  borderRadius: 'var(--radius-lg)',
                  display: 'grid',
                  placeItems: 'center',
                  background: item.colorBg,
                  color: item.color,
                }}
              >
                {item.icon}
              </div>
              <div
                style={{
                  flex: 1,
                  minWidth: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: 'var(--space-1)',
                }}
              >
                <h3 style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                  {item.title}
                </h3>
                <Badge variant={item.badge.variant}>{item.badge.label}</Badge>
              </div>
            </div>
            <p
              style={{
                margin: 0,
                fontSize: 'var(--font-size-sm)',
                color: 'var(--text-muted)',
                lineHeight: 'var(--line-height-normal)',
              }}
            >
              {item.description}
            </p>
          </Card>
        ))}
      </div>
    </section>
  )
}
