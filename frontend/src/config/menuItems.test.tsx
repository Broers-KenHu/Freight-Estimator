import { describe, expect, it } from 'vitest'
import { buildVisibleMenuItems } from './menuItems'

function labelsFor(permissions: string[]) {
  const can = (permission: string) => permissions.includes('*') || permissions.includes(permission)
  const canAny = (items: string[]) => items.some(can)
  const labels: string[] = []

  const visit = (items: ReturnType<typeof buildVisibleMenuItems>) => {
    for (const item of items) {
      if (!item) continue
      const record = item as { label?: unknown; children?: ReturnType<typeof buildVisibleMenuItems> }
      if (typeof record.label === 'string') labels.push(record.label)
      if (record.children) visit(record.children)
    }
  }

  visit(buildVisibleMenuItems(can, canAny))
  return labels
}

describe('buildVisibleMenuItems', () => {
  it('shows only menu entries allowed by permissions', () => {
    const labels = labelsFor(['quote.manual'])

    expect(labels).toContain('Manual Quote')
    expect(labels).not.toContain('Pricing')
    expect(labels).not.toContain('Users & Roles')
  })

  it('shows admin entries for wildcard users', () => {
    const labels = labelsFor(['*'])

    expect(labels).toContain('Manual Quote')
    expect(labels).toContain('Pricing')
    expect(labels).toContain('Users & Roles')
    expect(labels).toContain('Audit Logs')
  })
})
