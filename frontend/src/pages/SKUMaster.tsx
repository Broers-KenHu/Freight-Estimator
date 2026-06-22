import { ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { api, unpackList, type Paginated } from '../api/client'
import { StatusTag } from '../components/ResourceTable'

type SKURecord = {
  id: number
  sku: string
  description: string
  category: string
  unit_weight_kg: string
  length_cm: string
  width_cm: string
  height_cm: string
  active: boolean
  is_combo: boolean
  combo_type_label?: string
  combo_component_count?: number
  external_updated_at?: string | null
  last_synced_at?: string | null
  source_system?: string
}

type ComboComponentRow = {
  id: number
  combo_sku: string
  component_sku: string
  component_qty: string
  combo_title: string
  component_sku_snapshot?: SKURecord | null
}

type ComboMasterRecord = SKURecord & {
  components: ComboComponentRow[]
  component_count: number
  component_categories: string[]
  display_unit_weight_kg: string
  display_length_cm: string
  display_width_cm: string
  display_height_cm: string
}

type PagedRows<T> = {
  rows: T[]
  count: number
}

type TabKey = 'single' | 'combo'

const dateText = (value?: string | null) => (value ? value.replace('T', ' ').slice(0, 19) : '-')
const numberText = (value?: string | number | null, digits = 2) => Number(value || 0).toFixed(digits)
const dimensionText = (length?: string | number, width?: string | number, height?: string | number) =>
  `${numberText(length, 2)} / ${numberText(width, 2)} / ${numberText(height, 2)}`

async function fetchRows<T>(endpoint: string, search: string): Promise<PagedRows<T>> {
  const params = new URLSearchParams()
  if (search.trim()) params.set('search', search.trim())
  const suffix = params.toString() ? `?${params.toString()}` : ''
  const { data } = await api.get<Paginated<T> | T[]>(`${endpoint}${suffix}`)
  return {
    rows: unpackList(data),
    count: Array.isArray(data) ? data.length : data.count,
  }
}

function categoryCell(value?: string | string[]) {
  if (Array.isArray(value)) {
    return value.length ? value.join(', ') : '-'
  }
  return value || '-'
}

function comboTypeCell(value?: string) {
  if (!value) return <Tag>combo</Tag>
  const color = value === 'combo' ? 'purple' : value === 'AB件' ? 'geekblue' : value === '替代' ? 'orange' : 'default'
  return <Tag color={color}>{value}</Tag>
}

export function SKUMaster() {
  const [messageApi, contextHolder] = message.useMessage()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabKey>('single')
  const [singleSearchText, setSingleSearchText] = useState('')
  const [singleSearch, setSingleSearch] = useState('')
  const [comboSearchText, setComboSearchText] = useState('')
  const [comboSearch, setComboSearch] = useState('')

  const singleQuery = useQuery({
    queryKey: ['sku-master-single', singleSearch],
    queryFn: () => fetchRows<SKURecord>('/skus/single-master/', singleSearch),
  })
  const comboQuery = useQuery({
    queryKey: ['sku-master-combo', comboSearch],
    queryFn: () => fetchRows<ComboMasterRecord>('/skus/combo-master/', comboSearch),
  })

  useEffect(() => {
    const handle = window.setTimeout(() => setSingleSearch(singleSearchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [singleSearchText])

  useEffect(() => {
    const handle = window.setTimeout(() => setComboSearch(comboSearchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [comboSearchText])

  const activeSearchText = activeTab === 'single' ? singleSearchText : comboSearchText
  const activeSearchPlaceholder =
    activeTab === 'single' ? 'Search SKU, description, category' : 'Search combo SKU, title, category'
  const setActiveSearchText = (value: string) => {
    if (activeTab === 'single') {
      setSingleSearchText(value)
    } else {
      setComboSearchText(value)
    }
  }
  const submitActiveSearch = (value: string) => {
    if (activeTab === 'single') {
      setSingleSearch(value.trim())
    } else {
      setComboSearch(value.trim())
    }
  }

  const syncMutation = useMutation({
    mutationFn: async () => (await api.post('/skus/sync-from-wms/', {})).data,
    onSuccess: (data) => {
      const job = data.import_job
      messageApi.success(`SKU sync completed: ${job.success_rows} synced, ${job.error_rows} errors`)
      queryClient.invalidateQueries({ queryKey: ['sku-master-single'] })
      queryClient.invalidateQueries({ queryKey: ['sku-master-combo'] })
      queryClient.invalidateQueries({ queryKey: ['manual-quote-skus'] })
      queryClient.invalidateQueries({ queryKey: ['/skus/'] })
      queryClient.invalidateQueries({ queryKey: ['/import-jobs/'] })
    },
    onError: () => messageApi.error('SKU sync failed'),
  })

  const singleColumns = useMemo<ColumnsType<SKURecord>>(
    () => [
      { title: 'SKU', dataIndex: 'sku', width: 170, fixed: 'left', ellipsis: true },
      { title: 'Description', dataIndex: 'description', width: 260, ellipsis: true },
      { title: 'Category', dataIndex: 'category', width: 170, render: categoryCell },
      {
        title: 'Kg',
        dataIndex: 'unit_weight_kg',
        width: 100,
        align: 'right',
        render: (value) => <Typography.Text className="numeric-cell">{numberText(value, 3)}</Typography.Text>,
      },
      {
        title: 'L / W / H cm',
        width: 190,
        render: (_, record) => <Typography.Text className="numeric-cell">{dimensionText(record.length_cm, record.width_cm, record.height_cm)}</Typography.Text>,
      },
      { title: 'Source updated', dataIndex: 'external_updated_at', width: 180, render: dateText },
      { title: 'Last synced', dataIndex: 'last_synced_at', width: 180, render: dateText },
      { title: 'Active', dataIndex: 'active', width: 110, render: (value) => <StatusTag value={value} /> },
    ],
    [],
  )

  const componentColumns = useMemo<ColumnsType<ComboComponentRow>>(
    () => [
      { title: 'Component SKU', dataIndex: 'component_sku', width: 180, ellipsis: true },
      {
        title: 'Qty',
        dataIndex: 'component_qty',
        width: 90,
        align: 'right',
        render: (value) => <Typography.Text className="numeric-cell">{numberText(value, 3)}</Typography.Text>,
      },
      {
        title: 'Category',
        width: 170,
        render: (_, record) => categoryCell(record.component_sku_snapshot?.category),
      },
      {
        title: 'Kg',
        width: 100,
        align: 'right',
        render: (_, record) => <Typography.Text className="numeric-cell">{numberText(record.component_sku_snapshot?.unit_weight_kg, 3)}</Typography.Text>,
      },
      {
        title: 'L / W / H cm',
        width: 190,
        render: (_, record) => (
          <Typography.Text className="numeric-cell">
            {dimensionText(
              record.component_sku_snapshot?.length_cm,
              record.component_sku_snapshot?.width_cm,
              record.component_sku_snapshot?.height_cm,
            )}
          </Typography.Text>
        ),
      },
      { title: 'Description', width: 280, render: (_, record) => record.component_sku_snapshot?.description || '-' },
    ],
    [],
  )

  const comboColumns = useMemo<ColumnsType<ComboMasterRecord>>(
    () => [
      { title: 'Combo SKU', dataIndex: 'sku', width: 180, fixed: 'left', ellipsis: true },
      { title: 'Description / Title', dataIndex: 'description', width: 280, ellipsis: true },
      {
        title: 'Category',
        width: 190,
        render: (_, record) => categoryCell(record.category || record.component_categories),
      },
      { title: 'Type', dataIndex: 'combo_type_label', width: 110, render: comboTypeCell },
      {
        title: 'Components',
        dataIndex: 'component_count',
        width: 120,
        align: 'right',
        render: (value) => <Typography.Text className="numeric-cell">{value || 0}</Typography.Text>,
      },
      {
        title: 'Kg',
        dataIndex: 'display_unit_weight_kg',
        width: 100,
        align: 'right',
        render: (value) => <Typography.Text className="numeric-cell">{numberText(value, 3)}</Typography.Text>,
      },
      {
        title: 'L / W / H cm',
        width: 190,
        render: (_, record) => (
          <Typography.Text className="numeric-cell">
            {dimensionText(record.display_length_cm, record.display_width_cm, record.display_height_cm)}
          </Typography.Text>
        ),
      },
      { title: 'Last synced', dataIndex: 'last_synced_at', width: 180, render: dateText },
    ],
    [],
  )

  const toolbarCount = activeTab === 'single' ? singleQuery.data?.count || 0 : comboQuery.data?.count || 0

  return (
    <section className="page-surface sku-master-page">
      {contextHolder}
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>SKU Master</Typography.Title>
          <Typography.Text type="secondary">{toolbarCount} records</Typography.Text>
        </div>
        <Space>
          <Button icon={<SyncOutlined />} loading={syncMutation.isPending} onClick={() => syncMutation.mutate()}>
            Sync WMS SKU
          </Button>
          <Button
            aria-label="Reload"
            icon={<ReloadOutlined />}
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['sku-master-single'] })
              queryClient.invalidateQueries({ queryKey: ['sku-master-combo'] })
            }}
          />
        </Space>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          value={activeSearchText}
          placeholder={activeSearchPlaceholder}
          onChange={(event) => setActiveSearchText(event.target.value)}
          onSearch={submitActiveSearch}
        />
      </div>

      <Tabs
        className="sku-master-tabs"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as TabKey)}
        items={[
          {
            key: 'single',
            label: `Single SKU (${singleQuery.data?.count || 0})`,
            children: (
              <Space direction="vertical" size={12} className="sku-master-panel">
                <Table<SKURecord>
                  className="resource-table sku-master-table"
                  rowKey="id"
                  loading={singleQuery.isFetching}
                  columns={singleColumns}
                  dataSource={singleQuery.data?.rows || []}
                  size="middle"
                  scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 12, showSizeChanger: true }}
                />
              </Space>
            ),
          },
          {
            key: 'combo',
            label: `Combo SKU (${comboQuery.data?.count || 0})`,
            children: (
              <Space direction="vertical" size={12} className="sku-master-panel">
                <Table<ComboMasterRecord>
                  className="resource-table sku-master-table"
                  rowKey="id"
                  loading={comboQuery.isFetching}
                  columns={comboColumns}
                  dataSource={comboQuery.data?.rows || []}
                  size="middle"
                  scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 12, showSizeChanger: true }}
                  expandable={{
                    rowExpandable: (record) => record.components.length > 0,
                    expandedRowRender: (record) => (
                      <Table<ComboComponentRow>
                        className="combo-component-table"
                        rowKey={(row) => `${row.combo_sku}-${row.component_sku}`}
                        columns={componentColumns}
                        dataSource={record.components}
                        size="small"
                        pagination={false}
                        scroll={{ x: 'max-content' }}
                      />
                    ),
                  }}
                />
              </Space>
            ),
          },
        ]}
      />
    </section>
  )
}
