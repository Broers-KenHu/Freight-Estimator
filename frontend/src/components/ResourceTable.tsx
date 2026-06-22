import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Drawer, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, createResource, deleteResource, unpackList, updateResource, type Paginated } from '../api/client'

export type FieldConfig = {
  name: string
  label: string
  kind?: 'text' | 'number' | 'boolean' | 'textarea' | 'json' | 'select'
  required?: boolean
  options?: { label: string; value: number | string }[]
  allowClear?: boolean
}

export type FilterConfig = {
  name: string
  label: string
  kind?: 'text' | 'select' | 'boolean'
  options?: { label: string; value: number | string }[]
  placeholder?: string
}

type ResourceTableProps<T extends { id: number }> = {
  title: string
  endpoint: string
  columns: ColumnsType<T>
  fields: FieldConfig[]
  filters?: FilterConfig[]
  extraActions?: ReactNode
  rowActions?: (record: T) => ReactNode
  actionWidth?: number
  searchPlaceholder?: string
  compact?: boolean
}

type ResourceRows<T> = {
  rows: T[]
  count: number
}

function normalizePayload(values: Record<string, unknown>, fields: FieldConfig[]) {
  const payload = { ...values }
  for (const field of fields) {
    if (field.kind === 'json' && typeof payload[field.name] === 'string') {
      payload[field.name] = payload[field.name] ? JSON.parse(payload[field.name] as string) : {}
    }
  }
  return payload
}

function formInitialValues(record: Record<string, unknown> | null, fields: FieldConfig[]) {
  if (!record) return {}
  const values = { ...record }
  for (const field of fields) {
    if (field.kind === 'json' && values[field.name] && typeof values[field.name] !== 'string') {
      values[field.name] = JSON.stringify(values[field.name], null, 2)
    }
  }
  return values
}

async function fetchResourceRows<T>(
  endpoint: string,
  page: number,
  pageSize: number,
  search: string,
  filters: Record<string, number | string | undefined>,
): Promise<ResourceRows<T>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (search.trim()) params.set('search', search.trim())
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  const { data } = await api.get<Paginated<T> | T[]>(`${endpoint}?${params.toString()}`)
  const rows = unpackList(data)
  if (Array.isArray(data)) {
    const keyword = search.trim().toLowerCase()
    const filtered = keyword ? rows.filter((row) => JSON.stringify(row).toLowerCase().includes(keyword)) : rows
    return { rows: filtered, count: filtered.length }
  }
  return { rows, count: data.count }
}

export function ResourceTable<T extends { id: number }>({
  title,
  endpoint,
  columns,
  fields,
  filters = [],
  extraActions,
  rowActions,
  actionWidth,
  searchPlaceholder = 'Search this table',
  compact = true,
}: ResourceTableProps<T>) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<T | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [filterValues, setFilterValues] = useState<Record<string, number | string | undefined>>({})
  const [messageApi, contextHolder] = message.useMessage()
  const [form] = Form.useForm()
  const queryClient = useQueryClient()
  const queryKey = [endpoint, page, pageSize, search, filterValues]
  const { data, isFetching } = useQuery({
    queryKey,
    queryFn: () => fetchResourceRows<T>(endpoint, page, pageSize, search, filterValues),
  })
  const rows = data?.rows || []
  const totalRows = data?.count || 0

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const nextSearch = searchText.trim()
      setSearch((current) => {
        if (current === nextSearch) return current
        setPage(1)
        return nextSearch
      })
    }, 350)
    return () => window.clearTimeout(handle)
  }, [searchText])

  const saveMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => {
      const payload = normalizePayload(values, fields)
      return editing ? updateResource<T>(endpoint, editing.id, payload) : createResource<T>(endpoint, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [endpoint] })
      setOpen(false)
      setEditing(null)
      form.resetFields()
    },
    onError: () => messageApi.error(`Save ${title} failed`),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteResource(endpoint, id),
    onSuccess: () => {
      messageApi.success(`Deleted ${title} row`)
      queryClient.invalidateQueries({ queryKey: [endpoint] })
    },
    onError: () => messageApi.error(`Delete ${title} row failed`),
  })

  const tableColumns = useMemo<ColumnsType<T>>(
    () => [
      ...columns,
      ...(fields.length || rowActions
        ? [
            {
              title: 'Actions',
              width: actionWidth || (rowActions ? 260 : 96),
              fixed: 'right' as const,
              render: (_: unknown, record: T) => (
                <Space size={6} className="row-action-bar">
                  {rowActions?.(record)}
                  {fields.length > 0 && (
                    <>
                      <Button
                        aria-label="Edit"
                        icon={<EditOutlined />}
                        size="small"
                        onClick={() => {
                          setEditing(record)
                          form.setFieldsValue(formInitialValues(record as Record<string, unknown>, fields))
                          setOpen(true)
                        }}
                      />
                      <Popconfirm title="Delete this row?" onConfirm={() => deleteMutation.mutate(record.id)}>
                        <Button aria-label="Delete" danger icon={<DeleteOutlined />} size="small" />
                      </Popconfirm>
                    </>
                  )}
                </Space>
              ),
            },
          ]
        : []),
    ],
    [actionWidth, columns, deleteMutation, fields, form, rowActions],
  )

  return (
    <section className="page-surface">
      {contextHolder}
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>{title}</Typography.Title>
          <Typography.Text type="secondary">
            {totalRows.toLocaleString()} records{search ? ` matching "${search}"` : ''}
          </Typography.Text>
        </div>
        <Space wrap className="page-actions">
          {extraActions}
          <Button icon={<ReloadOutlined />} onClick={() => queryClient.invalidateQueries({ queryKey: [endpoint] })} />
          {fields.length > 0 && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setEditing(null)
                form.resetFields()
                setOpen(true)
              }}
            >
              New
            </Button>
          )}
        </Space>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          placeholder={searchPlaceholder}
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
          }}
        />
      </div>
      {filters.length > 0 && (
        <div className="resource-filter-bar">
          {filters.map((filter) =>
            filter.kind === 'select' || filter.kind === 'boolean' ? (
              <Select
                key={filter.name}
                allowClear
                className="resource-filter"
                optionFilterProp="label"
                options={
                  filter.kind === 'boolean'
                    ? [
                        { label: 'Yes', value: 'true' },
                        { label: 'No', value: 'false' },
                      ]
                    : filter.options || []
                }
                placeholder={filter.placeholder || filter.label}
                showSearch={filter.kind === 'select'}
                value={filterValues[filter.name]}
                onChange={(value) => {
                  setFilterValues((current) => ({ ...current, [filter.name]: value }))
                  setPage(1)
                }}
              />
            ) : (
              <Input
                key={filter.name}
                allowClear
                className="resource-filter"
                placeholder={filter.placeholder || filter.label}
                value={(filterValues[filter.name] as string | undefined) || ''}
                onChange={(event) => {
                  setFilterValues((current) => ({ ...current, [filter.name]: event.target.value }))
                  setPage(1)
                }}
              />
            ),
          )}
          <Button
            onClick={() => {
              setFilterValues({})
              setPage(1)
            }}
          >
            Clear filters
          </Button>
        </div>
      )}
      <Table<T>
        className="resource-table"
        rowKey="id"
        loading={isFetching}
        columns={tableColumns}
        dataSource={rows}
        size={compact ? 'small' : 'middle'}
        scroll={{ x: 'max-content' }}
        sticky
        pagination={{
          current: page,
          pageSize,
          total: totalRows,
          showSizeChanger: true,
          pageSizeOptions: [10, 25, 50, 100, 200],
          showTotal: (total, range) => `${range[0]}-${range[1]} of ${total.toLocaleString()}`,
        }}
        onChange={(pagination) => {
          setPage(pagination.current || 1)
          setPageSize(pagination.pageSize || 25)
        }}
      />
      <Drawer
        title={editing ? `Edit ${title}` : `New ${title}`}
        size="large"
        open={open}
        onClose={() => setOpen(false)}
        destroyOnHidden
        extra={
          <Button type="primary" loading={saveMutation.isPending} onClick={() => form.submit()}>
            Save
          </Button>
        }
      >
        <Form form={form} className="resource-drawer-form" layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          {fields.map((field) => (
            <Form.Item
              key={field.name}
              className={field.kind === 'json' || field.kind === 'textarea' ? 'form-item-full' : undefined}
              name={field.name}
              label={field.label}
              valuePropName={field.kind === 'boolean' ? 'checked' : 'value'}
              rules={[{ required: field.required }]}
            >
              {field.kind === 'boolean' ? (
                <Switch checkedChildren="On" unCheckedChildren="Off" />
              ) : field.kind === 'number' ? (
                <InputNumber className="full-width" />
              ) : field.kind === 'select' ? (
                <Select allowClear={field.allowClear} optionFilterProp="label" options={field.options || []} showSearch />
              ) : field.kind === 'textarea' || field.kind === 'json' ? (
                <Input.TextArea rows={field.kind === 'json' ? 8 : 4} />
              ) : (
                <Input />
              )}
            </Form.Item>
          ))}
        </Form>
      </Drawer>
    </section>
  )
}

export function StatusTag({ value }: { value: string | boolean }) {
  if (typeof value === 'boolean') {
    return <Tag color={value ? 'green' : 'default'}>{value ? 'Enabled' : 'Disabled'}</Tag>
  }
  const color = value === 'ACTIVE' || value === 'AVAILABLE' ? 'green' : value === 'DRAFT' ? 'blue' : value === 'NOT_AVAILABLE' ? 'red' : 'default'
  return <Tag color={color}>{value}</Tag>
}
