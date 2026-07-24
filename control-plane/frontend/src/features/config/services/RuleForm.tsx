import React, { useState, useEffect } from 'react'
import { Button, Field, NumberInput, Select, Switch } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { useOverlapCheck } from '../../../hooks/resources/useRules'
import type { RuleResponse, Protocol } from '../../../api/types'

interface RuleFormProps {
  rule?: RuleResponse
  existingRules: RuleResponse[]
  onSubmit: (data: {
    priority: number
    protocol: Protocol
    src_port_lo?: number | null
    src_port_hi?: number | null
    dst_port_lo?: number | null
    dst_port_hi?: number | null
    enabled: boolean
  }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
  serviceId: string
}

export function RuleForm({
  rule,
  existingRules,
  onSubmit,
  onCancel,
  isSubmitting = false,
  serviceId,
}: RuleFormProps) {
  const [priority, setPriority] = useState(rule?.priority != null ? String(rule.priority) : '')
  const [protocol, setProtocol] = useState<Protocol>(rule?.protocol ?? 'any')
  const [srcPortLo, setSrcPortLo] = useState(rule?.src_port_lo != null ? String(rule.src_port_lo) : '')
  const [srcPortHi, setSrcPortHi] = useState(rule?.src_port_hi != null ? String(rule.src_port_hi) : '')
  const [dstPortLo, setDstPortLo] = useState(rule?.dst_port_lo != null ? String(rule.dst_port_lo) : '')
  const [dstPortHi, setDstPortHi] = useState(rule?.dst_port_hi != null ? String(rule.dst_port_hi) : '')
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [overlapWarnings, setOverlapWarnings] = useState<string[]>([])

  const overlapCheckMutation = useOverlapCheck(serviceId)

  // Real-time overlap check
  useEffect(() => {
    if (!protocol) return

    const timer = setTimeout(() => {
      const payload: {
        protocol: Protocol
        src_port_lo?: number | null
        src_port_hi?: number | null
        dst_port_lo?: number | null
        dst_port_hi?: number | null
      } = {
        protocol,
        src_port_lo: srcPortLo ? Number(srcPortLo) : null,
        src_port_hi: srcPortHi ? Number(srcPortHi) : null,
        dst_port_lo: dstPortLo ? Number(dstPortLo) : null,
        dst_port_hi: dstPortHi ? Number(dstPortHi) : null,
      }

      // Skip overlap check if ports are malformed or invalid ranges
      if (payload.src_port_lo != null && (isNaN(payload.src_port_lo) || payload.src_port_lo < 1 || payload.src_port_lo > 65535)) return
      if (payload.src_port_hi != null && (isNaN(payload.src_port_hi) || payload.src_port_hi < 1 || payload.src_port_hi > 65535)) return
      if (payload.dst_port_lo != null && (isNaN(payload.dst_port_lo) || payload.dst_port_lo < 1 || payload.dst_port_lo > 65535)) return
      if (payload.dst_port_hi != null && (isNaN(payload.dst_port_hi) || payload.dst_port_hi < 1 || payload.dst_port_hi > 65535)) return
      if (payload.src_port_lo != null && payload.src_port_hi != null && payload.src_port_lo > payload.src_port_hi) return
      if (payload.dst_port_lo != null && payload.dst_port_hi != null && payload.dst_port_lo > payload.dst_port_hi) return

      overlapCheckMutation.mutate(payload, {
        onSuccess: (data) => {
          setOverlapWarnings(data.warnings ?? [])
        },
        onError: () => {
          setOverlapWarnings([])
        },
      })
    }, 400)

    return () => clearTimeout(timer)
  }, [protocol, srcPortLo, srcPortHi, dstPortLo, dstPortHi, serviceId, overlapCheckMutation])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}

    // Priority checks
    const prioVal = Number(priority)
    if (!priority.trim()) {
      nextErrors.priority = 'Priority is required'
    } else if (isNaN(prioVal) || prioVal < 1 || prioVal > 65535) {
      nextErrors.priority = 'Priority must be between 1 and 65535'
    } else {
      const isDuplicate = existingRules.some(
        (r) => r.priority === prioVal && r.id !== rule?.id
      )
      if (isDuplicate) {
        nextErrors.priority = 'Priority must be unique among this service\'s rules'
      }
    }

    // Rules count limit (16 maximum)
    if (!rule && existingRules.length >= 16) {
      nextErrors.priority = 'Maximum of 16 rules reached for this service'
    }

    // Port checks if not ICMP
    if (protocol !== 'icmp') {
      if (srcPortLo) {
        const val = Number(srcPortLo)
        if (isNaN(val) || val < 1 || val > 65535) {
          nextErrors.src_port_lo = 'Must be between 1 and 65535'
        }
      }
      if (srcPortHi) {
        const val = Number(srcPortHi)
        if (isNaN(val) || val < 1 || val > 65535) {
          nextErrors.src_port_hi = 'Must be between 1 and 65535'
        }
      }
      if (srcPortLo && srcPortHi && Number(srcPortLo) > Number(srcPortHi)) {
        nextErrors.src_port_lo = 'Start port cannot be greater than end port'
      }

      if (dstPortLo) {
        const val = Number(dstPortLo)
        if (isNaN(val) || val < 1 || val > 65535) {
          nextErrors.dst_port_lo = 'Must be between 1 and 65535'
        }
      }
      if (dstPortHi) {
        const val = Number(dstPortHi)
        if (isNaN(val) || val < 1 || val > 65535) {
          nextErrors.dst_port_hi = 'Must be between 1 and 65535'
        }
      }
      if (dstPortLo && dstPortHi && Number(dstPortLo) > Number(dstPortHi)) {
        nextErrors.dst_port_lo = 'Start port cannot be greater than end port'
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        priority: prioVal,
        protocol,
        src_port_lo: srcPortLo && protocol !== 'icmp' ? Number(srcPortLo) : null,
        src_port_hi: srcPortHi && protocol !== 'icmp' ? Number(srcPortHi) : null,
        dst_port_lo: dstPortLo && protocol !== 'icmp' ? Number(dstPortLo) : null,
        dst_port_hi: dstPortHi && protocol !== 'icmp' ? Number(dstPortHi) : null,
        enabled,
      })
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          const apiFieldErrors = fieldErrorsFrom422(err.detail)
          setErrors(apiFieldErrors)
        } else {
          setSubmitError(err.message)
        }
      } else if (err instanceof Error) {
        setSubmitError(err.message)
      } else {
        setSubmitError('An unexpected error occurred')
      }
    }
  }

  const isEdit = !!rule
  const isIcmp = protocol === 'icmp'

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div style={{
          color: 'var(--color-danger, #b42318)',
          backgroundColor: 'rgba(180, 35, 24, 0.1)',
          padding: 'var(--space-2)',
          border: '1px solid var(--color-danger, #b42318)',
          borderRadius: 'var(--radius-md)'
        }} role="alert">
          {submitError}
        </div>
      )}

      {overlapWarnings.length > 0 && (
        <div style={{
          backgroundColor: 'rgba(154, 103, 0, 0.1)',
          borderLeft: '4px solid var(--color-warning, #9a6700)',
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--font-size-sm)',
          color: 'var(--color-warning, #9a6700)'
        }}>
          <strong>Rule Overlap Warnings:</strong>
          <ul style={{ margin: 'var(--space-1) 0 0 0', paddingLeft: 'var(--space-4)', listStyleType: 'disc' }}>
            {overlapWarnings.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <Field label="Priority (1-65535)" error={errors.priority} required>
          <NumberInput
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            placeholder="e.g. 100"
            disabled={isSubmitting}
            min={1}
            max={65535}
          />
        </Field>

        <Field label="Protocol" required>
          <Select
            options={[
              { value: 'any', label: 'Any Protocol' },
              { value: 'tcp', label: 'TCP' },
              { value: 'udp', label: 'UDP' },
              { value: 'icmp', label: 'ICMP' },
            ]}
            value={protocol}
            onValueChange={(val) => setProtocol(val as Protocol)}
            disabled={isSubmitting}
          />
        </Field>
      </div>

      {!isIcmp && (
        <>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
            <h4 style={{ margin: '0 0 var(--space-2) 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Source Ports</h4>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
              <Field label="Start Port" error={errors.src_port_lo}>
                <NumberInput
                  value={srcPortLo}
                  onChange={(e) => setSrcPortLo(e.target.value)}
                  placeholder="e.g. 1024"
                  disabled={isSubmitting}
                  min={1}
                  max={65535}
                />
              </Field>
              <Field label="End Port" error={errors.src_port_hi}>
                <NumberInput
                  value={srcPortHi}
                  onChange={(e) => setSrcPortHi(e.target.value)}
                  placeholder="e.g. 65535"
                  disabled={isSubmitting}
                  min={1}
                  max={65535}
                />
              </Field>
            </div>
          </div>

          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
            <h4 style={{ margin: '0 0 var(--space-2) 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Destination Ports</h4>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
              <Field label="Start Port" error={errors.dst_port_lo}>
                <NumberInput
                  value={dstPortLo}
                  onChange={(e) => setDstPortLo(e.target.value)}
                  placeholder="e.g. 80"
                  disabled={isSubmitting}
                  min={1}
                  max={65535}
                />
              </Field>
              <Field label="End Port" error={errors.dst_port_hi}>
                <NumberInput
                  value={dstPortHi}
                  onChange={(e) => setDstPortHi(e.target.value)}
                  placeholder="e.g. 80"
                  disabled={isSubmitting}
                  min={1}
                  max={65535}
                />
              </Field>
            </div>
          </div>
        </>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
        <Switch
          id="rule-enabled-switch"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={isSubmitting}
        />
        <label htmlFor="rule-enabled-switch" style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500, cursor: 'pointer' }}>
          Enable Allow Rule
        </label>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)', borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          {isEdit ? 'Save Rule' : 'Create Rule'}
        </Button>
      </div>
    </form>
  )
}
