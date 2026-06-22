import { StatusTag, type FieldConfig } from '../components/ResourceTable'

export const boolRender = (value: boolean) => <StatusTag value={value} />

export const text = (name: string, label: string, required = false) => ({ name, label, required })

export const num = (name: string, label: string, required = false) => ({
  name,
  label,
  kind: 'number' as const,
  required,
})

export const flag = (name: string, label: string) => ({ name, label, kind: 'boolean' as const })

export const carrierNameRender = (value: unknown, record: Record<string, unknown>) => value || record.carrier_code || '-'

export const selectField = (
  name: string,
  label: string,
  options: { label: string; value: number | string }[],
  required = false,
  allowClear = !required,
): FieldConfig => ({
  name,
  label,
  kind: 'select',
  options,
  required,
  allowClear,
})
