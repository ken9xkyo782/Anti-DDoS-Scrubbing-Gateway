import React, { useState } from 'react'
import { Button, Field, Input, NumberInput, Select } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import type { ServiceResponse } from '../../../api/types'

interface ServiceFormProps {
  service?: ServiceResponse
  onSubmit: (data: {
    name: string
    cidr_or_ip: string
    mode: string
    vip_pps?: number | null
    vip_bps?: number | null
  }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function isValidCidrOrIp(val: string): boolean {
  const value = val.trim()
  if (!value) return false

  const ipv4Pattern = /^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/
  const ipv4CidrPattern = /^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\/([0-9]|[1-2][0-9]|3[0-2])$/
  
  const ipv6Pattern = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))$/
  const ipv6CidrPattern = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))\/([0-9]|[1-9][0-9]|1[0-1][0-9]|12[0-8])$/

  return ipv4Pattern.test(value) || ipv4CidrPattern.test(value) || ipv6Pattern.test(value) || ipv6CidrPattern.test(value);
}

export function ServiceForm({ service, onSubmit, onCancel, isSubmitting = false }: ServiceFormProps) {
  const [name, setName] = useState(service?.name ?? '')
  const [cidr, setCidr] = useState(service?.cidr_or_ip ?? '')
  const [mode, setMode] = useState<string>(service?.mode ?? 'allow-rule-only')
  const [vipPps, setVipPps] = useState<string>(service?.vip_pps != null ? String(service.vip_pps) : '')
  const [vipBps, setVipBps] = useState<string>(service?.vip_bps != null ? String(service.vip_bps) : '')

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!name.trim()) {
      nextErrors.name = 'Service name is required'
    }
    if (!cidr.trim()) {
      nextErrors.cidr_or_ip = 'CIDR or IP address is required'
    } else if (!isValidCidrOrIp(cidr)) {
      nextErrors.cidr_or_ip = 'Must be a valid IP address or CIDR block'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        name: name.trim(),
        cidr_or_ip: cidr.trim(),
        mode,
        vip_pps: vipPps ? Number(vipPps) : null,
        vip_bps: vipBps ? Number(vipBps) : null,
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

  const isEdit = !!service

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div style={{ color: 'var(--color-danger, #b42318)', padding: 'var(--space-2)', border: '1px solid', borderRadius: 'var(--radius-md)' }} role="alert">
          {submitError}
        </div>
      )}

      <Field label="Service Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. My API Gateway"
          disabled={isSubmitting}
        />
      </Field>

      <Field label="CIDR or IP Address" error={errors.cidr_or_ip} required>
        <Input
          value={cidr}
          onChange={(e) => setCidr(e.target.value)}
          placeholder="e.g. 192.0.2.0/24 or 2001:db8::/32"
          disabled={isSubmitting}
        />
      </Field>

      <Field label="Mode">
        <Select
          options={[{ value: 'allow-rule-only', label: 'Allow Rule Only' }]}
          value={mode}
          onValueChange={setMode}
          disabled={isSubmitting}
        />
      </Field>

      <Field label="VIP PPS Limit (Optional)" error={errors.vip_pps}>
        <NumberInput
          value={vipPps}
          onChange={(e) => setVipPps(e.target.value)}
          placeholder="e.g. 10000"
          disabled={isSubmitting}
        />
      </Field>

      <Field label="VIP BPS Limit (Optional)" error={errors.vip_bps}>
        <NumberInput
          value={vipBps}
          onChange={(e) => setVipBps(e.target.value)}
          placeholder="e.g. 10000000"
          disabled={isSubmitting}
        />
      </Field>

      {isEdit && service?.plan && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)', borderTop: '1px solid var(--border-color)', paddingTop: 'var(--space-4)' }}>
          <Field label="Committed Bandwidth">
            <NumberInput
              value={service.plan.committed_clean_gbps}
              disabled
              aria-label="Committed Bandwidth"
            />
          </Field>
          <Field label="Ceiling Bandwidth">
            <NumberInput
              value={service.plan.ceiling_clean_gbps}
              disabled
              aria-label="Ceiling Bandwidth"
            />
          </Field>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          {isEdit ? 'Save Changes' : 'Create'}
        </Button>
      </div>
    </form>
  )
}
