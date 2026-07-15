export interface ValidationErrorDetail {
  loc: (string | number)[]
  msg: string
  type: string
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message = `Request failed with status ${status}`,
    public readonly detail?: string | ValidationErrorDetail[]
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function fieldErrorsFrom422(detail: unknown): Record<string, string> {
  const errors: Record<string, string> = {}
  if (Array.isArray(detail)) {
    const list = detail as ValidationErrorDetail[]
    list.forEach((err) => {
      if (err && typeof err === 'object' && Array.isArray(err.loc)) {
        const loc = err.loc
        const bodyIdx = loc.indexOf('body')
        let fieldName = ''
        if (bodyIdx !== -1 && bodyIdx < loc.length - 1) {
          fieldName = String(loc[bodyIdx + 1])
        } else {
          fieldName = String(loc[loc.length - 1] || 'global')
        }
        errors[fieldName] = err.msg
      }
    })
  }
  return errors
}
