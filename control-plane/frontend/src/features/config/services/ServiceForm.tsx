import React, { useState } from 'react'
import { Button, Field, Input, NumberInput } from '../../../ui'
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
    service_pps?: number | null
    service_bps?: number | null
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
  // Mode is fixed to allow-rule-only and hidden from the form.
  const mode = service?.mode ?? 'allow-rule-only'
  const [vipPps, setVipPps] = useState<string>(
    service?.vip_pps != null ? String(service.vip_pps) : (service ? '' : '5000'),
  )
  const [vipBps, setVipBps] = useState<string>(
    service?.vip_bps != null ? String(service.vip_bps) : (service ? '' : '1000000000'),
  )
  // Service rate-limit caps clean (non-VIP) allowed traffic. Empty = unlimited (NULL) so a
  // new service is never accidentally rate-limited.
  const [servicePps, setServicePps] = useState<string>(
    service?.service_pps != null ? String(service.service_pps) : '',
  )
  const [serviceBps, setServiceBps] = useState<string>(
    service?.service_bps != null ? String(service.service_bps) : '',
  )

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

    if (vipPps.trim() !== '') {
      const ppsNum = Number(vipPps)
      if (isNaN(ppsNum) || ppsNum < 0) {
        nextErrors.vip_pps = 'VIP PPS Limit must be a non-negative number'
      }
    }

    if (vipBps.trim() !== '') {
      const bpsNum = Number(vipBps)
      if (isNaN(bpsNum) || bpsNum < 0) {
        nextErrors.vip_bps = 'VIP BPS Limit must be a non-negative number'
      }
    }

    if (servicePps.trim() !== '') {
      const ppsNum = Number(servicePps)
      if (isNaN(ppsNum) || ppsNum < 0) {
        nextErrors.service_pps = 'Service PPS Limit must be a non-negative number'
      }
    }

    if (serviceBps.trim() !== '') {
      const bpsNum = Number(serviceBps)
      if (isNaN(bpsNum) || bpsNum < 0) {
        nextErrors.service_bps = 'Service BPS Limit must be a non-negative number'
      }
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
        vip_pps: vipPps.trim() !== '' ? Number(vipPps) : null,
        vip_bps: vipBps.trim() !== '' ? Number(vipBps) : null,
        service_pps: servicePps.trim() !== '' ? Number(servicePps) : null,
        service_bps: serviceBps.trim() !== '' ? Number(serviceBps) : null,
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

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
        <h4 style={{ margin: '0 0 var(--space-1) 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
          VIP Ceiling (Optional)
        </h4>
        <p style={{ margin: '0 0 var(--space-2) 0', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
          Caps whitelisted / bypass traffic. Leave blank for no ceiling.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
          <Field label="VIP PPS Limit" error={errors.vip_pps}>
            <NumberInput
              value={vipPps}
              onChange={(e) => setVipPps(e.target.value)}
              placeholder="e.g. 5000"
              disabled={isSubmitting}
              aria-label="VIP PPS Limit"
            />
          </Field>
          <Field label="VIP BPS Limit (Bytes/sec)" error={errors.vip_bps}>
            <NumberInput
              value={vipBps}
              onChange={(e) => setVipBps(e.target.value)}
              placeholder="e.g. 1000000000"
              disabled={isSubmitting}
              aria-label="VIP BPS Limit"
            />
          </Field>
        </div>
      </div>

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
        <h4 style={{ margin: '0 0 var(--space-1) 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
          Service Rate Limit (Optional)
        </h4>
        <p style={{ margin: '0 0 var(--space-2) 0', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
          Caps clean (non-VIP) allowed traffic for the whole service. Leave blank for no limit.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
          <Field label="Service PPS Limit" error={errors.service_pps}>
            <NumberInput
              value={servicePps}
              onChange={(e) => setServicePps(e.target.value)}
              placeholder="e.g. 200000"
              disabled={isSubmitting}
              aria-label="Service PPS Limit"
            />
          </Field>
          <Field label="Service BPS Limit (Bytes/sec)" error={errors.service_bps}>
            <NumberInput
              value={serviceBps}
              onChange={(e) => setServiceBps(e.target.value)}
              placeholder="e.g. 2000000000"
              disabled={isSubmitting}
              aria-label="Service BPS Limit"
            />
          </Field>
        </div>
      </div>

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
