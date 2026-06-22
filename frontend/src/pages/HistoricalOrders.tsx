import { PlayCircleOutlined, SyncOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Space, Table, Tag, Typography, Upload, message, type UploadProps } from 'antd'
import { useEffect, useState } from 'react'
import { api, listResource } from '../api/client'
import { ResourceTable, StatusTag } from '../components/ResourceTable'
import type { Platform, Warehouse } from '../types'

type ImportJob = {
  id: number
  job_type: string
  status: string
  total_rows: number
  success_rows: number
  error_rows: number
  created_at?: string
}

type HistoricalOrder = {
  id: number
  order_no: string
  erp_order_no?: string
  erp_owner_order_no?: string
  external_order_no?: string
  platform_order_no?: string
  shipping_option?: string
  source_order_type?: string
  platform_code?: string
  platform_name?: string
  warehouse_code?: string
  warehouse_name?: string
  source_warehouse_code?: string
  tracking_numbers?: string[]
  suburb: string
  state: string
  postcode: string
  actual_carrier?: string
  actual_freight?: string | null
  postage_shipping_estimated_amount?: string | null
  source_estimated_freight?: string | null
  source_estimated_carrier?: string
  source_estimated_service?: string
  best_estimated_freight?: string | null
  best_estimated_carrier_name?: string
  best_estimated_carrier_code?: string
  item_count?: number
  quote_run_count?: number
  order_date?: string | null
  source_updated_at?: string | null
}

const orderTypeOptions = [
  { label: 'Platform orders', value: 'PLATFORM' },
  { label: 'Third-party orders', value: 'THIRD_PARTY' },
  { label: 'Manual orders', value: 'MANUAL' },
  { label: 'ERP other', value: 'ERP' },
]

function money(value?: string | null) {
  if (!value) return '-'
  const amount = Number(value)
  return Number.isFinite(amount) ? `$${amount.toFixed(2)}` : value
}

function erpEstimateMoney(value?: string | null) {
  if (!value) return '-'
  const amount = Number(value)
  return Number.isFinite(amount) ? `$${(amount * 1.1).toFixed(2)}` : value
}

export function HistoricalOrders() {
  const queryClient = useQueryClient()
  const [messageApi, contextHolder] = message.useMessage()
  const { data: platforms = [] } = useQuery({ queryKey: ['/platforms/'], queryFn: () => listResource<Platform>('/platforms/') })
  const { data: warehouses = [] } = useQuery({ queryKey: ['/warehouses/'], queryFn: () => listResource<Warehouse>('/warehouses/') })
  const [jobSearchText, setJobSearchText] = useState('')
  const [jobSearch, setJobSearch] = useState('')
  const { data: jobs = [], isFetching } = useQuery({
    queryKey: ['import-jobs', jobSearch],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '200' })
      if (jobSearch) params.set('search', jobSearch)
      return listResource<ImportJob>(`/order-import-jobs/?${params.toString()}`)
    },
  })

  useEffect(() => {
    const handle = window.setTimeout(() => setJobSearch(jobSearchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [jobSearchText])

  const runQuotes = useMutation({
    mutationFn: async (id: number) => (await api.post(`/order-import-jobs/${id}/run-quotes/`)).data,
    onSuccess: (data) => {
      messageApi.success(`Created ${data.quote_run_ids.length} quote runs`)
      queryClient.invalidateQueries({ queryKey: ['import-jobs'] })
      queryClient.invalidateQueries({ queryKey: ['/historical-orders/'] })
    },
  })

  const syncErpOrders = useMutation({
    mutationFn: async () => (await api.post('/historical-orders/sync-from-erp/', {})).data,
    onSuccess: (data) => {
      const job = data.import_job
      messageApi.success(`ERP orders synced: ${Number(job.success_rows || 0).toLocaleString()} rows`)
      queryClient.invalidateQueries({ queryKey: ['import-jobs'] })
      queryClient.invalidateQueries({ queryKey: ['/historical-orders/'] })
    },
    onError: () => messageApi.error('ERP order sync failed'),
  })

  const uploadProps: UploadProps = {
    name: 'file',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      const form = new FormData()
      form.append('file', file as File)
      try {
        await api.post('/order-import-jobs/', form)
        queryClient.invalidateQueries({ queryKey: ['import-jobs'] })
        queryClient.invalidateQueries({ queryKey: ['/historical-orders/'] })
        onSuccess?.('ok')
      } catch (error) {
        onError?.(error as Error)
      }
    },
  }

  const platformOptions = platforms.map((platform) => ({ value: platform.id, label: `${platform.name || platform.code}` }))
  const warehouseOptions = warehouses.map((warehouse) => ({ value: warehouse.id, label: `${warehouse.code} - ${warehouse.name}` }))

  return (
    <>
      {contextHolder}
      <ResourceTable<HistoricalOrder>
        title="Imported Orders"
        endpoint="/historical-orders/"
        fields={[]}
        searchPlaceholder="Search ERP order, platform order, tracking, postcode, carrier"
        extraActions={
          <>
            <Upload {...uploadProps}>
              <Button icon={<UploadOutlined />}>Upload CSV</Button>
            </Upload>
            <Button icon={<SyncOutlined />} loading={syncErpOrders.isPending} onClick={() => syncErpOrders.mutate()}>
              Sync ERP Orders
            </Button>
          </>
        }
        filters={[
          { name: 'platform', label: 'Sales platform', kind: 'select', options: platformOptions },
          { name: 'warehouse', label: 'Warehouse', kind: 'select', options: warehouseOptions },
          { name: 'source_order_type', label: 'Order type', kind: 'select', options: orderTypeOptions },
          { name: 'tracking', label: 'Tracking', placeholder: 'Tracking / consignment no' },
          { name: 'state', label: 'State' },
          { name: 'postcode', label: 'Postcode' },
          { name: 'actual_carrier', label: 'Courier' },
          { name: 'source_estimated_carrier', label: 'ERP estimated courier' },
        ]}
        columns={[
          { title: 'External Order No', dataIndex: 'external_order_no', width: 160, render: (value, record) => value || record.order_no || '-' },
          { title: 'Platform Order No', dataIndex: 'platform_order_no', width: 160, render: (value) => value || '-' },
          { title: 'Owner Order', dataIndex: 'erp_owner_order_no', width: 150, render: (value) => value || '-' },
          {
            title: 'Tracking',
            dataIndex: 'tracking_numbers',
            width: 190,
            render: (values: string[] | undefined) => (values?.length ? values.join(', ') : '-'),
          },
          { title: 'Type', dataIndex: 'source_order_type', width: 120, render: (value) => <Tag>{value || 'ERP'}</Tag> },
          { title: 'Platform', dataIndex: 'platform_name', width: 180, render: (value, record) => value || record.platform_code || '-' },
          { title: 'Warehouse', dataIndex: 'warehouse_code', width: 110, render: (value, record) => value || record.source_warehouse_code || '-' },
          { title: 'Destination', width: 240, render: (_, record) => `${record.suburb || '-'}, ${record.state || '-'} ${record.postcode || ''}` },
          { title: 'Ship method', dataIndex: 'shipping_option', width: 150, render: (value) => value || '-' },
          { title: 'Courier', dataIndex: 'actual_carrier', width: 150, render: (value) => value || '-' },
          { title: 'Postage est. inc GST', dataIndex: 'postage_shipping_estimated_amount', width: 140, align: 'right', render: erpEstimateMoney },
          { title: 'ERP est. inc GST', dataIndex: 'source_estimated_freight', width: 130, align: 'right', render: erpEstimateMoney },
          { title: 'System est.', dataIndex: 'best_estimated_freight', width: 120, align: 'right', render: money },
          { title: 'Best carrier', dataIndex: 'best_estimated_carrier_name', width: 160, render: (value, record) => value || record.best_estimated_carrier_code || '-' },
          { title: 'Items', dataIndex: 'item_count', width: 80, align: 'right', render: (value) => value || 0 },
          { title: 'Quote runs', dataIndex: 'quote_run_count', width: 100, align: 'right', render: (value) => value || 0 },
          { title: 'Order date', dataIndex: 'order_date', width: 120, render: (value) => value || '-' },
          { title: 'Source updated', dataIndex: 'source_updated_at', width: 190, render: (value) => value || '-' },
        ]}
      />

      <section className="page-surface section-block">
        <div className="page-toolbar">
          <div>
            <Typography.Title level={2}>Import Jobs</Typography.Title>
            <Typography.Text type="secondary">CSV uploads and ERP sync runs.</Typography.Text>
        </div>
      </div>
        <div className="list-search-row">
          <Input.Search
            allowClear
            className="resource-search"
            placeholder="Search job type or status"
            value={jobSearchText}
            onChange={(event) => setJobSearchText(event.target.value)}
            onSearch={(value) => setJobSearch(value.trim())}
          />
        </div>
        <Table<ImportJob>
          rowKey="id"
          className="resource-table"
          size="small"
          loading={isFetching}
          dataSource={jobs}
          scroll={{ x: 'max-content' }}
          columns={[
            { title: 'Job', dataIndex: 'id', width: 80 },
            { title: 'Type', dataIndex: 'job_type', width: 160 },
            { title: 'Status', dataIndex: 'status', width: 130, render: (value) => <StatusTag value={value} /> },
            { title: 'Rows', dataIndex: 'total_rows', width: 100, align: 'right', render: (value) => Number(value || 0).toLocaleString() },
            { title: 'Imported', dataIndex: 'success_rows', width: 110, align: 'right', render: (value) => Number(value || 0).toLocaleString() },
            { title: 'Errors', dataIndex: 'error_rows', width: 100, align: 'right' },
            { title: 'Created', dataIndex: 'created_at', width: 190, render: (value) => value || '-' },
            {
              title: 'Actions',
              width: 130,
              render: (_, record) => (
                <Space>
                  <Button size="small" icon={<PlayCircleOutlined />} onClick={() => runQuotes.mutate(record.id)}>
                    Run quotes
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </section>
    </>
  )
}
