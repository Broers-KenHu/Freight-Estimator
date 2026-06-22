import { ApiOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Descriptions, Drawer, Input, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { api, type Paginated } from '../api/client'

type LspApiQuoteOption = {
  id: number
  option_index: number
  carrier_code?: string
  carrier_name?: string
  courier_code?: string
  courier_name?: string
  service_code?: string
  service_name?: string
  can_shipping: boolean
  shipping_cost?: string | null
  carrier_shipping_cost?: string | null
  calc_mode?: string
  remark?: string
}

type PackageItem = {
  sku?: string
  qty?: number | string
  title?: string
  weight?: number | string
  length?: number | string
  width?: number | string
  height?: number | string
}

type RequestPackage = {
  weight?: number | string
  length?: number | string
  width?: number | string
  height?: number | string
  items?: PackageItem[]
}

type LspApiQuoteSnapshot = {
  id: number
  quote_at?: string | null
  status?: string
  status_summary?: string
  historical_order?: number | null
  historical_order_no?: string
  platform_code?: string
  platform_name?: string
  source_platform_id?: string
  erp_order_no?: string
  erp_owner_order_no?: string
  external_order_no?: string
  platform_order_no?: string
  booking_tracking_no?: string
  display_order_no?: string
  display_order_type?: 'ERP_ORDER' | 'LSP_REFERENCE' | string
  display_tracking_no?: string
  display_warehouse_code?: string
  display_warehouse_name?: string
  display_warehouse_source?: 'WMS' | 'LSP' | string
  lsp_reference_no?: string
  lsp_order_code?: string
  lsp_shipment_code?: string
  request_id?: string
  quote_id?: string
  warehouse_code?: string
  strategy_code?: string
  destination_suburb?: string
  destination_state?: string
  destination_postcode?: string
  predicted_carrier_code?: string
  predicted_carrier_name?: string
  predicted_service_code?: string
  predicted_service_name?: string
  predicted_shipping_cost?: string | null
  predicted_carrier_shipping_cost?: string | null
  predict_price?: string | null
  owner_price?: string | null
  erp_estimated_freight?: string | null
  erp_postage_estimated_freight?: string | null
  best_lsp_amount?: string | null
  package_count: number
  quote_option_count: number
  internal_log_item_count?: number
  request_summary_json?: {
    packages?: RequestPackage[]
    to?: Record<string, string>
    requestId?: string
    shipmentCode?: string
    strategyCode?: string
    warehouseCode?: string
  }
  response_summary_json?: Record<string, unknown>
  options: LspApiQuoteOption[]
}

type LspQuoteTaskLogItem = {
  id: number
  source_external_id: string
  item_scope?: string
  log_action?: string
  calc_mode?: string
  rate_type?: string
  carrier_agent_code?: string
  agent_code?: string
  carrier_code?: string
  channel_code?: string
  service_level?: string
  can_shipping: boolean
  shipping_cost?: string | null
  shipping_cost_with_tax?: string | null
  surcharge?: string | null
  estimated_days?: string | null
  failed_reason?: string
  log_created_at?: string | null
}

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

function dateText(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function costValue(record: LspApiQuoteSnapshot) {
  return record.best_lsp_amount || record.predicted_shipping_cost || record.predict_price || record.owner_price
}

function orderLabel(record: LspApiQuoteSnapshot) {
  return record.display_order_no || record.lsp_reference_no || record.lsp_order_code || '-'
}

function warehouseLabel(record: LspApiQuoteSnapshot) {
  return record.display_warehouse_name || record.display_warehouse_code || record.warehouse_code || '-'
}

function LspExpandedComparison({ record }: { record: LspApiQuoteSnapshot }) {
  const logsQuery = useQuery({
    queryKey: ['lsp-quote-log-items-expanded', record.id],
    queryFn: async () => {
      const params = new URLSearchParams({ snapshot: String(record.id), page_size: '120' })
      const { data } = await api.get<Paginated<LspQuoteTaskLogItem>>(`/lsp-quote-log-items/?${params.toString()}`)
      return data
    },
  })

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Descriptions size="small" column={4} bordered>
        <Descriptions.Item label={record.display_order_no ? 'ERP Order' : 'LSP Ref'}>{orderLabel(record)}</Descriptions.Item>
        <Descriptions.Item label="Tracking">{record.display_tracking_no || '-'}</Descriptions.Item>
        <Descriptions.Item label="Warehouse">
          {warehouseLabel(record)} {record.display_warehouse_source ? <Tag>{record.display_warehouse_source}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="LSP Price">{money(costValue(record))}</Descriptions.Item>
      </Descriptions>

      <div>
        <Typography.Title level={5}>LSP Returned Options</Typography.Title>
        <Table<LspApiQuoteOption>
          rowKey="id"
          size="small"
          dataSource={[...(record.options || [])].sort((a, b) => Number(b.can_shipping) - Number(a.can_shipping) || Number(a.shipping_cost || 0) - Number(b.shipping_cost || 0))}
          pagination={false}
          scroll={{ x: 'max-content' }}
          columns={[
            { title: '#', dataIndex: 'option_index', width: 50 },
            { title: 'Carrier', width: 200, render: (_, item) => item.courier_name || item.carrier_name || item.courier_code || item.carrier_code || '-' },
            { title: 'Service', width: 200, render: (_, item) => item.service_name || item.service_code || '-' },
            { title: 'Can Ship', dataIndex: 'can_shipping', width: 90, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
            { title: 'Shipping Cost', dataIndex: 'shipping_cost', width: 120, align: 'right', render: money },
            { title: 'Carrier Cost', dataIndex: 'carrier_shipping_cost', width: 120, align: 'right', render: money },
            { title: 'Remark', dataIndex: 'remark', width: 260, render: (value) => value || '-' },
          ]}
        />
      </div>

      <div>
        <Typography.Title level={5}>LSP Internal Comparison</Typography.Title>
        <Table<LspQuoteTaskLogItem>
          rowKey="id"
          size="small"
          loading={logsQuery.isFetching}
          dataSource={logsQuery.data?.results || []}
          pagination={false}
          scroll={{ x: 'max-content' }}
          columns={[
            { title: 'Action', dataIndex: 'log_action', width: 80, render: (value) => value || '-' },
            { title: 'Scope', dataIndex: 'item_scope', width: 90, render: (value) => value || '-' },
            { title: 'Agent', width: 100, render: (_, item) => item.agent_code || item.carrier_agent_code || '-' },
            { title: 'Carrier', dataIndex: 'carrier_code', width: 140, render: (value) => value || '-' },
            { title: 'Channel', dataIndex: 'channel_code', width: 130, render: (value) => value || '-' },
            { title: 'Service', dataIndex: 'service_level', width: 130, render: (value) => value || '-' },
            { title: 'Can Ship', dataIndex: 'can_shipping', width: 90, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
            { title: 'Cost', dataIndex: 'shipping_cost', width: 100, align: 'right', render: money },
            { title: 'Surcharge', dataIndex: 'surcharge', width: 100, align: 'right', render: money },
            { title: 'Reason', dataIndex: 'failed_reason', width: 260, render: (value) => value || '-' },
          ]}
        />
      </div>
    </Space>
  )
}

export function LspApiQuotes() {
  const queryClient = useQueryClient()
  const [messageApi, contextHolder] = message.useMessage()
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [selected, setSelected] = useState<LspApiQuoteSnapshot>()

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setPage(1)
      setSearch(searchText.trim())
    }, 350)
    return () => window.clearTimeout(handle)
  }, [searchText])

  const quotesQuery = useQuery({
    queryKey: ['lsp-api-quotes', search, page, pageSize],
    queryFn: async () => {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), ordering: '-quote_at' })
      if (search) params.set('search', search)
      const { data } = await api.get<Paginated<LspApiQuoteSnapshot>>(`/lsp-api-quotes/?${params.toString()}`)
      return data
    },
  })

  const syncMutation = useMutation({
    mutationFn: async () => (await api.post('/lsp-api-quotes/sync-from-lsp/', {})).data,
    onSuccess: (data) => {
      const job = data.import_job
      messageApi.success(`LSP API quotes synced: ${Number(job.success_rows || 0).toLocaleString()} rows`)
      queryClient.invalidateQueries({ queryKey: ['lsp-api-quotes'] })
      queryClient.invalidateQueries({ queryKey: ['import-jobs'] })
    },
    onError: () => messageApi.error('LSP API quote sync failed'),
  })

  const syncLogsMutation = useMutation({
    mutationFn: async () => (await api.post('/lsp-quote-log-items/sync-from-lsp/', {})).data,
    onSuccess: (data) => {
      const job = data.import_job
      messageApi.success(`LSP internal logs synced: ${Number(job.success_rows || 0).toLocaleString()} logs`)
      queryClient.invalidateQueries({ queryKey: ['lsp-api-quotes'] })
      queryClient.invalidateQueries({ queryKey: ['lsp-quote-log-items'] })
    },
    onError: () => messageApi.error('LSP internal log sync failed'),
  })

  const internalLogsQuery = useQuery({
    queryKey: ['lsp-quote-log-items', selected?.id],
    enabled: Boolean(selected?.id),
    queryFn: async () => {
      const params = new URLSearchParams({ snapshot: String(selected?.id), page_size: '200' })
      const { data } = await api.get<Paginated<LspQuoteTaskLogItem>>(`/lsp-quote-log-items/?${params.toString()}`)
      return data
    },
  })

  const packageItems = useMemo(() => {
    const rows: Array<PackageItem & { key: string; package_no: number }> = []
    selected?.request_summary_json?.packages?.forEach((pkg, packageIndex) => {
      const items = pkg.items?.length ? pkg.items : [{ weight: pkg.weight, length: pkg.length, width: pkg.width, height: pkg.height }]
      items.forEach((item, itemIndex) => rows.push({ ...item, package_no: packageIndex + 1, key: `${packageIndex}-${itemIndex}` }))
    })
    return rows
  }, [selected])

  return (
    <>
      {contextHolder}
      <section className="page-surface section-block">
        <div className="page-toolbar">
          <div>
            <Typography.Title level={2}>LSP API Quotes</Typography.Title>
            <Typography.Text type="secondary">Historical LSP API request/response prices matched where ERP references are available.</Typography.Text>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} loading={syncMutation.isPending} onClick={() => syncMutation.mutate()}>
              Sync API Quotes
            </Button>
            <Button icon={<ReloadOutlined />} loading={syncLogsMutation.isPending} onClick={() => syncLogsMutation.mutate()}>
              Sync Internal Logs
            </Button>
          </Space>
        </div>
        <div className="list-search-row">
          <Input.Search
            allowClear
            className="resource-search"
            placeholder="Search ERP order, tracking, platform order, LSP ref, shipment, request id, carrier, suburb, postcode"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            onSearch={(value) => {
              setPage(1)
              setSearch(value.trim())
            }}
          />
        </div>
        <Table<LspApiQuoteSnapshot>
          rowKey="id"
          className="resource-table"
          size="small"
          loading={quotesQuery.isFetching}
          dataSource={quotesQuery.data?.results || []}
          scroll={{ x: 'max-content' }}
          pagination={{
            current: page,
            pageSize,
            total: quotesQuery.data?.count || 0,
            showSizeChanger: true,
            showTotal: (total) => `${total.toLocaleString()} records`,
          }}
          onChange={(pagination) => {
            setPage(pagination.current || 1)
            setPageSize(pagination.pageSize || 50)
          }}
          expandable={{
            expandedRowRender: (record) => <LspExpandedComparison record={record} />,
            rowExpandable: (record) => Boolean(record.quote_option_count || record.internal_log_item_count),
          }}
          columns={[
            { title: 'Quote At', dataIndex: 'quote_at', width: 170, render: dateText },
            {
              title: 'Order',
              width: 250,
              render: (_, record) => (
                <Space direction="vertical" size={0}>
                  <Space size={6}>
                    <Typography.Text>{orderLabel(record)}</Typography.Text>
                    <Tag color={record.display_order_no ? 'green' : 'default'}>{record.display_order_no ? 'ERP Order' : 'LSP Ref'}</Tag>
                  </Space>
                  <Typography.Text type="secondary">{record.platform_order_no || record.erp_owner_order_no || record.lsp_shipment_code || '-'}</Typography.Text>
                </Space>
              ),
            },
            { title: 'Tracking', dataIndex: 'display_tracking_no', width: 160, render: (value) => value || '-' },
            { title: 'Platform', dataIndex: 'platform_name', width: 160, render: (value, record) => value || record.platform_code || record.source_platform_id || '-' },
            {
              title: 'Warehouse',
              width: 150,
              render: (_, record) => (
                <Space size={4}>
                  <Typography.Text>{warehouseLabel(record)}</Typography.Text>
                  {record.display_warehouse_source ? <Tag>{record.display_warehouse_source}</Tag> : null}
                </Space>
              ),
            },
            {
              title: 'Destination',
              width: 210,
              render: (_, record) => `${record.destination_suburb || '-'}, ${record.destination_state || '-'} ${record.destination_postcode || ''}`,
            },
            {
              title: 'Predicted Carrier',
              width: 220,
              render: (_, record) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text>{record.predicted_carrier_name || record.predicted_carrier_code || '-'}</Typography.Text>
                  <Typography.Text type="secondary">{record.predicted_service_name || record.predicted_service_code || '-'}</Typography.Text>
                </Space>
              ),
            },
            { title: 'LSP API Price', width: 120, align: 'right', render: (_, record) => money(costValue(record)) },
            { title: 'ERP Est. inc GST', dataIndex: 'erp_estimated_freight', width: 130, align: 'right', render: erpEstimateMoney },
            { title: 'Options', dataIndex: 'quote_option_count', width: 90, align: 'right' },
            { title: 'Internal Logs', dataIndex: 'internal_log_item_count', width: 120, align: 'right', render: (value) => value || 0 },
            {
              title: 'Match',
              width: 110,
              render: (_, record) => (record.historical_order ? <Tag color="green">ERP linked</Tag> : <Tag>LSP only</Tag>),
            },
            {
              title: 'Actions',
              width: 100,
              fixed: 'right',
              render: (_, record) => (
                <Button size="small" icon={<ApiOutlined />} onClick={() => setSelected(record)}>
                  Review
                </Button>
              ),
            },
          ]}
        />
      </section>

      <Drawer
        title="Historical LSP API Quote"
        open={Boolean(selected)}
        onClose={() => setSelected(undefined)}
        width="min(1180px, 92vw)"
        destroyOnClose
      >
        {selected && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions size="small" column={3} bordered>
              <Descriptions.Item label={selected.display_order_no ? 'ERP Order' : 'LSP Ref'}>{orderLabel(selected)}</Descriptions.Item>
              <Descriptions.Item label="Shipment">{selected.lsp_shipment_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="Tracking">{selected.display_tracking_no || '-'}</Descriptions.Item>
              <Descriptions.Item label="Request ID">{selected.request_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="Destination">
                {selected.destination_suburb || '-'}, {selected.destination_state || '-'} {selected.destination_postcode || ''}
              </Descriptions.Item>
              <Descriptions.Item label="Warehouse">
                {warehouseLabel(selected)} {selected.display_warehouse_source ? <Tag>{selected.display_warehouse_source}</Tag> : null}
              </Descriptions.Item>
              <Descriptions.Item label="Strategy">{selected.strategy_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="Predicted">
                {selected.predicted_carrier_name || selected.predicted_carrier_code || '-'} / {selected.predicted_service_name || selected.predicted_service_code || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="LSP API Price">{money(costValue(selected))}</Descriptions.Item>
              <Descriptions.Item label="ERP Est. inc GST">{erpEstimateMoney(selected.erp_estimated_freight)}</Descriptions.Item>
            </Descriptions>

            <div>
              <Typography.Title level={4}>Returned Options</Typography.Title>
              <Table<LspApiQuoteOption>
                rowKey="id"
                size="small"
                dataSource={[...(selected.options || [])].sort((a, b) => Number(b.can_shipping) - Number(a.can_shipping) || Number(a.shipping_cost || 0) - Number(b.shipping_cost || 0))}
                pagination={false}
                scroll={{ x: 'max-content' }}
                columns={[
                  { title: '#', dataIndex: 'option_index', width: 60 },
                  { title: 'Carrier', width: 220, render: (_, record) => record.courier_name || record.carrier_name || record.courier_code || record.carrier_code || '-' },
                  { title: 'Service', width: 220, render: (_, record) => record.service_name || record.service_code || '-' },
                  { title: 'Can Ship', dataIndex: 'can_shipping', width: 100, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
                  { title: 'Shipping Cost', dataIndex: 'shipping_cost', width: 130, align: 'right', render: money },
                  { title: 'Carrier Cost', dataIndex: 'carrier_shipping_cost', width: 130, align: 'right', render: money },
                  { title: 'Calc Mode', dataIndex: 'calc_mode', width: 110, render: (value) => value || '-' },
                  { title: 'Remark', dataIndex: 'remark', width: 280, render: (value) => value || '-' },
                ]}
              />
            </div>

            <div>
              <Typography.Title level={4}>Internal Calculation Logs</Typography.Title>
              <Table<LspQuoteTaskLogItem>
                rowKey="id"
                size="small"
                loading={internalLogsQuery.isFetching}
                dataSource={internalLogsQuery.data?.results || []}
                pagination={false}
                scroll={{ x: 'max-content' }}
                columns={[
                  { title: 'Action', dataIndex: 'log_action', width: 90, render: (value) => value || '-' },
                  { title: 'Scope', dataIndex: 'item_scope', width: 90, render: (value) => value || '-' },
                  { title: 'Agent', width: 120, render: (_, record) => record.agent_code || record.carrier_agent_code || '-' },
                  { title: 'Carrier', dataIndex: 'carrier_code', width: 160, render: (value) => value || '-' },
                  { title: 'Channel', dataIndex: 'channel_code', width: 150, render: (value) => value || '-' },
                  { title: 'Service', dataIndex: 'service_level', width: 150, render: (value) => value || '-' },
                  { title: 'Can Ship', dataIndex: 'can_shipping', width: 90, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
                  { title: 'Cost', dataIndex: 'shipping_cost', width: 110, align: 'right', render: money },
                  { title: 'Cost inc tax', dataIndex: 'shipping_cost_with_tax', width: 120, align: 'right', render: money },
                  { title: 'Surcharge', dataIndex: 'surcharge', width: 110, align: 'right', render: money },
                  { title: 'Days', dataIndex: 'estimated_days', width: 80, align: 'right', render: (value) => value || '-' },
                  { title: 'Reason', dataIndex: 'failed_reason', width: 320, render: (value) => value || '-' },
                ]}
              />
              {(internalLogsQuery.data?.count || 0) > (internalLogsQuery.data?.results.length || 0) && (
                <Typography.Text type="secondary">
                  Showing first {internalLogsQuery.data?.results.length || 0} of {internalLogsQuery.data?.count?.toLocaleString()} internal log items.
                </Typography.Text>
              )}
            </div>

            <div>
              <Typography.Title level={4}>Request Packages</Typography.Title>
              <Table
                rowKey="key"
                size="small"
                dataSource={packageItems}
                pagination={false}
                scroll={{ x: 'max-content' }}
                columns={[
                  { title: 'Pkg', dataIndex: 'package_no', width: 70 },
                  { title: 'SKU', dataIndex: 'sku', width: 180, render: (value) => value || '-' },
                  { title: 'Title', dataIndex: 'title', width: 320, render: (value) => value || '-' },
                  { title: 'Qty', dataIndex: 'qty', width: 80, align: 'right', render: (value) => value || '-' },
                  { title: 'Kg', dataIndex: 'weight', width: 90, align: 'right', render: (value) => value || '-' },
                  {
                    title: 'L/W/H cm',
                    width: 140,
                    render: (_, record) => `${record.length || '-'} / ${record.width || '-'} / ${record.height || '-'}`,
                  },
                ]}
              />
            </div>
          </Space>
        )}
      </Drawer>
    </>
  )
}
