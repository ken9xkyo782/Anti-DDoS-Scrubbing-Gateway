import type { CSSProperties } from 'react'

// Data-mark colors are literal hex (SVG `fill` attributes do not resolve CSS
// custom properties). These read acceptably on both the light and dark
// surfaces. Axis/grid/tooltip chrome is themed via CSS in dashboard.module.css
// and the inline styles below (inline CSS does resolve custom properties).
export const CHART_COLORS = {
  clean: '#2ea043',
  drop: '#e5484d',
}

// Cohesive categorical palette for drop-reason breakdowns.
export const DROP_REASON_PALETTE = ['#e5484d', '#f5a524', '#3b82f6', '#8b5cf6', '#14b8a6']

export const tooltipContentStyle: CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  boxShadow: 'var(--shadow-2)',
  color: 'var(--text)',
  fontSize: 'var(--font-size-xs)',
}

export const tooltipItemStyle: CSSProperties = { color: 'var(--text)' }
export const tooltipLabelStyle: CSSProperties = { color: 'var(--text-muted)' }
