import React, { useState } from 'react'
import { Button, Field, Input, Select, Switch } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import type { FeedSourceResponse, FeedFormat } from '../../../api/types'

export interface FeedFormPayload {
  name: string
  url: string
  sync_interval_seconds: number
  format: FeedFormat
  enabled: boolean
  // Optional so an edit can omit it to keep the existing stored credential.
  credential_env_var?: string | null
}

interface FeedFormProps {
  feed?: FeedSourceResponse
  onSubmit: (data: FeedFormPayload) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function FeedForm({ feed, onSubmit, onCancel, isSubmitting = false }: FeedFormProps) {
  const [name, setName] = useState(feed?.name ?? '')
  const [url, setUrl] = useState(feed?.url ?? '')
  const [syncInterval, setSyncInterval] = useState(feed?.sync_interval_seconds?.toString() ?? '3600')
  const [format, setFormat] = useState<FeedFormat>(feed?.format ?? 'line_list')
  const [enabled, setEnabled] = useState(feed?.enabled ?? true)
  const [credentialEnvVar, setCredentialEnvVar] = useState(feed?.has_credential ? 'STORED_SECRET_ENV_VAR' : '') // dummy placeholder if has_credential, wait, backend only accepts environmental variable name if update or create. Let's see. If the user doesn't update it, do they send it? Let's check how credential_env_var is defined in the db.
  // Wait, does the threat feed source model store the credential_env_var or not?
  // Let's inspect the model details or just allow editing the credential_env_var string!
  // In schemas/feeds.py: FeedSourceCreateRequest has credential_env_var: str | None = None
  // FeedSourceResponse has has_credential: bool (indicating if credential_env_var is set in db)
  // Let's view models.py ThreatFeedSource definition to see if it stores credential_env_var.
  // Actually, we can check. Wait, yes, the database stores the name of the env var, e.g. "credential_env_var" string in ThreatFeedSource.
  // But wait, the API response has `has_credential: bool`.
  // So the response doesn't expose the actual name of the environment variable (for safety/security), but the update request payload allows sending `credential_env_var` string or `null` to clear it.
  // If we edit a feed, how do we show the credential_env_var?
  // We can show it as an empty field or placeholder if they want to override it.
  // Let's check: if we leave it blank, does it mean "do not change" or "clear"?
  // Let's look at `update_source` in `services/feeds.py`:
  // `if _has(payload, "credential_env_var"): locked.credential_env_var = _validate_credential_env_var(_value(payload, "credential_env_var"))`
  // So if we don't include `credential_env_var` in the PUT payload, it stays unchanged.
  // And if we include it as `null`, it will be set to `None` (cleared).
  // So we can have:
  // - A text input for "Credential Environment Variable" (optional).
  // - If editing, we can show a checkbox "Update credentials" or just default it to empty, and explain "Leave blank to keep existing credential" if it currently has a credential, or just let them type the new value.
  // Let's do that! That's extremely clear and standard.
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isEdit = !!feed

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!name.trim()) {
      nextErrors.name = 'Feed name is required'
    } else if (name.trim().length > 255) {
      nextErrors.name = 'Feed name must be between 1 and 255 characters'
    }

    // URL validation
    const trimmedUrl = url.trim()
    if (!trimmedUrl) {
      nextErrors.url = 'URL is required'
    } else {
      try {
        const parsed = new URL(trimmedUrl)
        if (parsed.protocol.toLowerCase() !== 'https:') {
          nextErrors.url = 'Feed source URL must be a valid HTTPS URL'
        } else if (!parsed.hostname) {
          nextErrors.url = 'Feed source URL must be a valid HTTPS URL'
        } else if (parsed.username || parsed.password || parsed.hash || trimmedUrl.includes('#')) {
          nextErrors.url = 'Feed source URL must be HTTPS without userinfo or fragments'
        }
      } catch {
        nextErrors.url = 'Feed source URL must be a valid HTTPS URL'
      }
    }

    // Sync Interval validation
    const intervalInt = parseInt(syncInterval, 10)
    if (isNaN(intervalInt) || intervalInt < 300 || intervalInt > 604800) {
      nextErrors.sync_interval_seconds = 'sync_interval_seconds must be between 300 and 604800'
    }

    // Credential Env Var validation
    const trimmedEnv = credentialEnvVar.trim()
    if (trimmedEnv) {
      const regex = /^[A-Z][A-Z0-9_]{0,127}$/
      if (!regex.test(trimmedEnv)) {
        nextErrors.credential_env_var = 'credential_env_var must be an uppercase environment variable name'
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      // Build request payload
      const payload: FeedFormPayload = {
        name: name.trim(),
        url: trimmedUrl,
        sync_interval_seconds: intervalInt,
        format,
        enabled,
        credential_env_var: trimmedEnv || null,
      }

      // If editing and credentialEnvVar was left empty, but it already has a credential,
      // we can omit or send null? Wait, if we want to keep the credential, does sending null clear it?
      // Yes! In update_source: `locked.credential_env_var = _validate_credential_env_var(_value(payload, "credential_env_var"))`
      // So if `credential_env_var` is null in the request, it will clear it.
      // Therefore, if it is edit mode, and user doesn't touch/input the env var, we shouldn't send it in the PUT payload if they want to keep it.
      // Or we can let the user explicitly toggle "Clear credentials" if they want, or check if they changed it.
      // Let's do this: if editing, only include `credential_env_var` in payload if the user has modified/entered something, or if they checked a "Remove Credentials" option.
      // Wait, let's keep it simple:
      // Let's show a text input for `Credential Environment Variable` and if editing, add a checkbox/help text: "Only specify a new variable name to change or overwrite the existing credentials". If they clear it entirely and save, we set it to null. But what if they want to keep it?
      // Let's check: if we have a state `changeCredential` (boolean) default to false for edit mode, and if checked, we allow entering a value or setting to null. If not checked, we exclude `credential_env_var` from the payload!
      // This is extremely safe and clear. Let's implement that!
      const finalPayload: FeedFormPayload = { ...payload }
      if (isEdit) {
        if (!changeCredential) {
          delete finalPayload.credential_env_var
        }
      }

      await onSubmit(finalPayload)
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

  // Edit mode credentials helper
  const [changeCredential, setChangeCredential] = useState(false)

  const formatOptions = [
    { value: 'line_list', label: 'Line List (New-line separated CIDRs)' },
  ]

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div
          style={{
            color: 'var(--color-danger, #b42318)',
            padding: 'var(--space-2)',
            border: '1px solid',
            borderRadius: 'var(--radius-md)',
          }}
          role="alert"
        >
          {submitError}
        </div>
      )}

      <Field label="Feed Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Spamhaus DROP List"
          disabled={isSubmitting}
          aria-label="Feed Name"
        />
      </Field>

      <Field label="Feed URL" error={errors.url} required>
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/blocklist.txt"
          disabled={isSubmitting}
          aria-label="Feed URL"
        />
      </Field>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <Field label="Sync Interval (seconds)" error={errors.sync_interval_seconds} required>
          <Input
            type="number"
            value={syncInterval}
            onChange={(e) => setSyncInterval(e.target.value)}
            placeholder="e.g. 3600 (1 hour)"
            disabled={isSubmitting}
            min={300}
            max={604800}
            aria-label="Sync Interval"
          />
        </Field>

        <Field label="Feed Format" required>
          <Select
            options={formatOptions}
            value={format}
            onValueChange={(val) => setFormat(val as FeedFormat)}
            disabled={isSubmitting}
            aria-label="Feed Format"
          />
        </Field>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', margin: 'var(--space-2) 0' }}>
        <Switch
          id="feed-enabled"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={isSubmitting}
        />
        <label htmlFor="feed-enabled" style={{ fontWeight: 500, cursor: 'pointer' }}>
          Enable Feed Sync schedule
        </label>
      </div>

      {isEdit && feed?.has_credential && !changeCredential ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 'var(--font-sm)', color: 'var(--text-muted)' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <span>This feed is configured with a credential variable.</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setChangeCredential(true)}
              style={{ marginLeft: 'auto' }}
            >
              Change Credential
            </Button>
          </div>
        </div>
      ) : (
        <Field
          label="Credential Env Var (Optional)"
          error={errors.credential_env_var}
          hint="Environment variable containing the bearer token or basic auth credentials."
        >
          <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
            <Input
              value={credentialEnvVar}
              onChange={(e) => setCredentialEnvVar(e.target.value)}
              placeholder="e.g. SPAMHAUS_TOKEN"
              disabled={isSubmitting}
              aria-label="Credential Env Var"
              style={{ flex: 1 }}
            />
            {isEdit && (
              <Button type="button" variant="secondary" onClick={() => { setChangeCredential(false); setCredentialEnvVar('') }}>
                Cancel Change
              </Button>
            )}
          </div>
        </Field>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          {isEdit ? 'Save Changes' : 'Create Feed'}
        </Button>
      </div>
    </form>
  )
}
