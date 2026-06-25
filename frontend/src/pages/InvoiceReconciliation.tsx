import { DownloadOutlined, EyeOutlined, FilterOutlined, SyncOutlined, UploadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Input,
  Select,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
  type UploadProps,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, listResource, type Paginated } from '../api/client'
import type { InvoiceReconciliationBatch, InvoiceReconciliationItem } from '../types'

type DataView =
  | 'all'
  | 'matched'
  | 'exceptions'
  | 'overcharge'
  | 'undercharge'
  | 'unmatched'
  | 'disputes'
  | 'missing_erp'
  | 'missing_system'
  | 'with_system'

type ColumnGroup = 'identity' | 'amounts' | 'differences' | 'status' | 'reasons'

type ItemFilters = {
  order_no: string
  tracking: string
  invoice_no: string
  carrier_name: string
  invoice_source_name: string
  has_erp_estimate: string
  has_system_estimate: string
  has_order: string
}

type SummaryBreakdown = {
  total: number
  invoice_source_id?: number | null
  invoice_source__name?: string | null
  carrier_id?: number | null
  carrier__name?: string | null
}

type ReconciliationSummary = {
  total: number
  matched: number
  exceptions: number
  unmatched: number
  overcharge: number
  undercharge: number
  disputes: number
  missing_erp: number
  missing_system: number
  actual_total?: string | null
  erp_estimate_total?: string | null
  system_estimate_total?: string | null
  erp_variance_total?: string | null
  system_variance_total?: string | null
  by_invoice_source: SummaryBreakdown[]
  by_carrier: SummaryBreakdown[]
}

const defaultItemFilters: ItemFilters = {
  order_no: '',
  tracking: '',
  invoice_no: '',
  carrier_name: '',
  invoice_source_name: '',
  has_erp_estimate: '',
  has_system_estimate: '',
  has_order: '',
}

const dataViewOptions: { label: string; value: DataView }[] = [
  { label: 'All', value: 'all' },
  { label: 'Matched', value: 'matched' },
  { label: 'Exceptions', value: 'exceptions' },
  { label: 'Overcharge', value: 'overcharge' },
  { label: 'Undercharge', value: 'undercharge' },
  { label: 'Unmatched', value: 'unmatched' },
  { label: 'Disputes', value: 'disputes' },
  { label: 'Missing ERP', value: 'missing_erp' },
  { label: 'Missing System', value: 'missing_system' },
  { label: 'With System', value: 'with_system' },
]

const columnGroupOptions: { label: string; value: ColumnGroup }[] = [
  { label: 'Identity', value: 'identity' },
  { label: 'Amounts', value: 'amounts' },
  { label: 'Differences', value: 'differences' },
  { label: 'Status', value: 'status' },
  { label: 'Reasons', value: 'reasons' },
]

const estimateOptions = [
  { label: 'All', value: '' },
  { label: 'Yes', value: 'true' },
  { label: 'No', value: 'false' },
]

const money = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `$${numeric.toFixed(2)}` : '-'
}

const erpEstimateMoney = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `$${numeric.toFixed(2)}` : '-'
}

const erpVarianceMoney = (_value: string | null, record: InvoiceReconciliationItem) => {
  const actual = Number(record.actual_freight)
  const estimate = Number(record.estimated_freight_inc_gst ?? record.estimated_freight)
  if (!Number.isFinite(actual) || !Number.isFinite(estimate)) return '-'
  return `$${(actual - estimate).toFixed(2)}`
}

const erpVariancePercent = (_value: string | null, record: InvoiceReconciliationItem) => {
  const actual = Number(record.actual_freight)
  const estimate = Number(record.estimated_freight_inc_gst ?? record.estimated_freight)
  if (!Number.isFinite(actual) || !Number.isFinite(estimate) || estimate === 0) return '-'
  return `${(((actual - estimate) / estimate) * 100).toFixed(1)}%`
}

const percent = (value?: string | null) => (value === null || value === undefined ? '-' : `${Number(value).toFixed(1)}%`)
const compactText = (value?: string | null) => value || '-'
const detailText = (value?: string | number | null) => (value === null || value === undefined || value === '' ? '-' : String(value))

const filenameFromDisposition = (value?: string) => {
  const match = value?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  return match ? decodeURIComponent(match[1]) : ''
}

function buildItemParams(
  batch: InvoiceReconciliationBatch,
  page: number,
  pageSize: number,
  searchValue: string,
  dataView: DataView,
  filters: ItemFilters,
  includePagination = true,
) {
  const params: Record<string, string | number | undefined> = { batch: batch.id }
  if (includePagination) {
    params.page = page
    params.page_size = pageSize
  }
  if (searchValue) params.search = searchValue
  if (dataView !== 'all') params.data_view = dataView
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params[key] = value
  })
  return params
}

function DetailDescriptions({ record }: { record: InvoiceReconciliationItem }) {
  const match = record.invoice_match_detail || {}
  const order = record.order_detail || {}
  const amount = record.amount_detail || {}
  return (
    <div className="reconciliation-detail-panel">
      <Descriptions size="small" bordered column={4} title="Order and Invoice Match">
        <Descriptions.Item label="ERP Order">{detailText(order.erp_order_no || record.order_no)}</Descriptions.Item>
        <Descriptions.Item label="Platform Order">{detailText(order.platform_order_no || match.platform_order_no)}</Descriptions.Item>
        <Descriptions.Item label="3rd Party Order">{detailText(order.third_party_order_no || match.third_party_order_no)}</Descriptions.Item>
        <Descriptions.Item label="Tracking">{detailText(match.tracking_no || record.consignment_no)}</Descriptions.Item>
        <Descriptions.Item label="Warehouse">{detailText(order.warehouse_code || match.warehouse_owner_code)}</Descriptions.Item>
        <Descriptions.Item label="Platform">{detailText(order.platform_name || order.platform_code)}</Descriptions.Item>
        <Descriptions.Item label="Shipping Option">{detailText(order.shipping_option)}</Descriptions.Item>
        <Descriptions.Item label="ERP Carrier">{detailText(order.actual_carrier || match.carrier_name)}</Descriptions.Item>
        <Descriptions.Item label="Invoice Source" span={2}>
          {detailText(match.source_label || record.invoice_source_name)}
        </Descriptions.Item>
        <Descriptions.Item label="Match Tier">{detailText(match.match_tier)}</Descriptions.Item>
        <Descriptions.Item label="Match Method">{detailText(match.match_method)}</Descriptions.Item>
        <Descriptions.Item label="Reason" span={4}>
          {detailText(match.match_reason || record.reason)}
        </Descriptions.Item>
      </Descriptions>

      <Descriptions size="small" bordered column={4} title="Amount Basis">
        <Descriptions.Item label="ERP Est source">{money(amount.erp_estimate_source || amount.erp_estimate_ex_gst)}</Descriptions.Item>
        <Descriptions.Item label="ERP Est inc GST">{money(amount.erp_estimate_inc_gst)}</Descriptions.Item>
        <Descriptions.Item label="System Est inc GST">{money(amount.system_estimate_inc_gst)}</Descriptions.Item>
        <Descriptions.Item label="Invoice Actual inc GST">{money(amount.actual_invoice_inc_gst || record.actual_freight)}</Descriptions.Item>
        <Descriptions.Item label="ERP Diff inc GST">{money(amount.erp_variance_inc_gst)}</Descriptions.Item>
        <Descriptions.Item label="ERP Diff %">{percent(amount.erp_variance_percent || record.variance_percent)}</Descriptions.Item>
        <Descriptions.Item label="System Diff inc GST">{money(amount.system_variance_inc_gst)}</Descriptions.Item>
        <Descriptions.Item label="System Diff %">{percent(amount.system_variance_percent || record.system_variance_percent)}</Descriptions.Item>
      </Descriptions>

      <Descriptions size="small" bordered column={4} title="InvoiceReader Source Detail">
        <Descriptions.Item label="Source Row ID">{detailText(match.source_external_id)}</Descriptions.Item>
        <Descriptions.Item label="Invoice No">{detailText(match.invoice_no || record.invoice_no)}</Descriptions.Item>
        <Descriptions.Item label="Carrier Channel">{detailText(match.carrier_channel)}</Descriptions.Item>
        <Descriptions.Item label="Account">{detailText(match.carrier_channel_account)}</Descriptions.Item>
        <Descriptions.Item label="Amount ex GST">{money(match.amount_ex_gst)}</Descriptions.Item>
        <Descriptions.Item label="Amount inc GST">{money(match.amount_inc_gst)}</Descriptions.Item>
        <Descriptions.Item label="ERP Carrier Freight">{money(match.erp_carrier_freight)}</Descriptions.Item>
        <Descriptions.Item label="Matched At">{detailText(match.matched_at)}</Descriptions.Item>
      </Descriptions>
    </div>
  )
}

export function InvoiceReconciliation() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<InvoiceReconciliationBatch | null>(null)
  const [selectedItems, setSelectedItems] = useState<InvoiceReconciliationItem[]>([])
  const [selectedTotal, setSelectedTotal] = useState(0)
  const [selectedPage, setSelectedPage] = useState(1)
  const [selectedPageSize, setSelectedPageSize] = useState(100)
  const [loadingItems, setLoadingItems] = useState(false)
  const [exportingBatchId, setExportingBatchId] = useState<number | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [lastSyncSummary, setLastSyncSummary] = useState<string>('')
  const [batchSearchText, setBatchSearchText] = useState('')
  const [batchSearch, setBatchSearch] = useState('')
  const [itemSearchText, setItemSearchText] = useState('')
  const [itemSearch, setItemSearch] = useState('')
  const [dataView, setDataView] = useState<DataView>('all')
  const [itemFilters, setItemFilters] = useState<ItemFilters>(defaultItemFilters)
  const [visibleColumnGroups, setVisibleColumnGroups] = useState<ColumnGroup[]>(['identity', 'amounts', 'differences', 'status', 'reasons'])

  const { data = [], isFetching } = useQuery({
    queryKey: ['invoice-reconciliation-batches', batchSearch],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '200' })
      if (batchSearch) params.set('search', batchSearch)
      return listResource<InvoiceReconciliationBatch>(`/invoice-reconciliation-batches/?${params.toString()}`)
    },
  })

  const summaryQuery = useQuery({
    queryKey: ['invoice-reconciliation-summary', selected?.id, dataView, itemSearch, itemFilters],
    enabled: Boolean(selected),
    queryFn: async () => {
      if (!selected) throw new Error('No batch selected')
      const { data } = await api.get<ReconciliationSummary>('/invoice-reconciliation-items/summary/', {
        params: buildItemParams(selected, 1, 1, itemSearch, dataView, itemFilters, false),
      })
      return data
    },
  })

  useEffect(() => {
    const handle = window.setTimeout(() => setBatchSearch(batchSearchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [batchSearchText])

  const uploadProps: UploadProps = {
    name: 'file',
    accept: '.csv,.xlsx',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      const form = new FormData()
      form.append('file', file as File)
      try {
        await api.post('/invoice-reconciliation-batches/', form)
        queryClient.invalidateQueries({ queryKey: ['invoice-reconciliation-batches'] })
        message.success('Invoice reconciliation completed')
        onSuccess?.('ok')
      } catch (error) {
        onError?.(error as Error)
      }
    },
  }

  const syncFromSqlServer = async () => {
    setSyncing(true)
    setLastSyncSummary('')
    try {
      const { data: result } = await api.post('/invoice-reconciliation-batches/sync-from-sqlserver/', {})
      const job = result.import_job
      const summary = job
        ? `${job.success_rows || 0} rows imported, ${job.error_rows || 0} skipped across ${(result.batches || []).length} source batches`
        : 'Invoice sync completed'
      setLastSyncSummary(summary)
      queryClient.invalidateQueries({ queryKey: ['invoice-reconciliation-batches'] })
      message.success('SQL Server invoice sync completed')
    } catch {
      message.error('SQL Server invoice sync failed')
    } finally {
      setSyncing(false)
    }
  }

  const loadBatchItems = useCallback(
    async (
      batch: InvoiceReconciliationBatch,
      page = 1,
      pageSize = selectedPageSize,
      searchValue = itemSearch,
      viewValue = dataView,
      filters = itemFilters,
    ) => {
      setSelected(batch)
      setLoadingItems(true)
      try {
        const { data: payload } = await api.get<Paginated<InvoiceReconciliationItem> | InvoiceReconciliationItem[]>('/invoice-reconciliation-items/', {
          params: buildItemParams(batch, page, pageSize, searchValue, viewValue, filters),
        })
        const rows = Array.isArray(payload) ? payload : payload.results
        setSelectedItems(rows)
        setSelectedTotal(Array.isArray(payload) ? rows.length : payload.count)
        setSelectedPage(page)
        setSelectedPageSize(pageSize)
      } finally {
        setLoadingItems(false)
      }
    },
    [dataView, itemFilters, itemSearch, selectedPageSize],
  )

  const openBatch = async (batch: InvoiceReconciliationBatch) => {
    setSelectedItems([])
    setSelectedTotal(0)
    setItemSearchText('')
    setItemSearch('')
    setDataView('all')
    setItemFilters(defaultItemFilters)
    await loadBatchItems(batch, 1, selectedPageSize, '', 'all', defaultItemFilters)
  }

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const nextSearch = itemSearchText.trim()
      setItemSearch(nextSearch)
      if (selected) void loadBatchItems(selected, 1, selectedPageSize, nextSearch, dataView, itemFilters)
    }, 350)
    return () => window.clearTimeout(handle)
  }, [dataView, itemFilters, itemSearchText, loadBatchItems, selected, selectedPageSize])

  const itemColumns = useMemo<ColumnsType<InvoiceReconciliationItem>>(() => {
    const groups = new Set(visibleColumnGroups)
    const columns: ColumnsType<InvoiceReconciliationItem> = []
    if (groups.has('identity')) {
      columns.push(
        { title: 'ERP Order', dataIndex: 'order_no', width: 170, ellipsis: true, render: compactText },
        { title: 'Tracking', dataIndex: 'consignment_no', width: 120, ellipsis: true, render: compactText },
        { title: 'Invoice', dataIndex: 'invoice_no', width: 118, ellipsis: true, render: compactText },
        { title: 'Source', dataIndex: 'invoice_source_name', width: 170, ellipsis: true, render: compactText },
        { title: 'Carrier', dataIndex: 'carrier_name', width: 140, ellipsis: true, render: (value: string, record) => value || record.carrier_code || '-' },
        { title: 'Service', dataIndex: 'carrier_service_name', width: 150, ellipsis: true, render: (value: string, record) => value || record.carrier_service_code || '-' },
      )
    }
    if (groups.has('amounts')) {
      columns.push(
        { title: 'ERP Est. inc GST', dataIndex: 'estimated_freight_inc_gst', width: 112, align: 'right', className: 'money-cell', render: erpEstimateMoney },
        { title: 'System Est.', dataIndex: 'system_estimated_freight', width: 92, align: 'right', className: 'money-cell', render: money },
        { title: 'Actual inc GST', dataIndex: 'actual_freight', width: 100, align: 'right', className: 'money-cell', render: money },
      )
    }
    if (groups.has('differences')) {
      columns.push(
        { title: 'ERP Diff', dataIndex: 'variance_amount', width: 86, align: 'right', className: 'money-cell', render: erpVarianceMoney },
        { title: 'ERP %', dataIndex: 'variance_percent', width: 74, align: 'right', render: erpVariancePercent },
        { title: 'Sys Diff', dataIndex: 'system_variance_amount', width: 86, align: 'right', className: 'money-cell', render: money },
        { title: 'Sys %', dataIndex: 'system_variance_percent', width: 72, align: 'right', render: percent },
      )
    }
    if (groups.has('status')) {
      columns.push(
        { title: 'Status', dataIndex: 'match_status', width: 92, render: (value: string) => <Tag color={value === 'MATCHED' ? 'green' : value === 'EXCEPTION' ? 'red' : 'default'}>{value}</Tag> },
        { title: 'Type', dataIndex: 'variance_type', width: 104, render: (value: string) => <Tag color={value === 'OVERCHARGE' ? 'red' : value === 'UNDERCHARGE' ? 'gold' : value === 'OK' ? 'green' : 'default'}>{value}</Tag> },
        { title: 'Dispute', dataIndex: 'dispute_recommended', width: 70, render: (value: boolean) => <Tag color={value ? 'red' : 'default'}>{value ? 'Y' : 'N'}</Tag> },
      )
    }
    if (groups.has('reasons')) {
      columns.push(
        { title: 'System Reason', dataIndex: 'system_estimate_reason', width: 210, ellipsis: true, render: compactText },
        { title: 'Reason', dataIndex: 'reason', width: 360, ellipsis: true, render: compactText },
      )
    }
    return columns
  }, [visibleColumnGroups])

  const updateFilter = (key: keyof ItemFilters, value: string) => {
    const next = { ...itemFilters, [key]: value }
    setItemFilters(next)
    if (selected) void loadBatchItems(selected, 1, selectedPageSize, itemSearch, dataView, next)
  }

  const changeDataView = (value: DataView) => {
    setDataView(value)
    if (selected) void loadBatchItems(selected, 1, selectedPageSize, itemSearch, value, itemFilters)
  }

  const clearItemFilters = () => {
    setItemFilters(defaultItemFilters)
    setDataView('all')
    setItemSearchText('')
    setItemSearch('')
    if (selected) void loadBatchItems(selected, 1, selectedPageSize, '', 'all', defaultItemFilters)
  }

  const downloadBatchExport = async (batch: InvoiceReconciliationBatch, scope: 'all' | 'disputes' = 'all') => {
    setExportingBatchId(batch.id)
    try {
      const { data: blob, headers } = await api.get<Blob>(`/invoice-reconciliation-batches/${batch.id}/export/`, {
        params: { scope },
        responseType: 'blob',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filenameFromDisposition(headers['content-disposition']) || `invoice-reconciliation-${batch.id}${scope === 'disputes' ? '-disputes' : ''}.xlsx`
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('Export failed')
    } finally {
      setExportingBatchId(null)
    }
  }

  const summary = summaryQuery.data

  return (
    <section className="page-surface">
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Invoice Reconciliation</Typography.Title>
          <Typography.Text type="secondary">Review InvoiceReader order matches, ERP estimates, system estimates, and invoice actuals.</Typography.Text>
        </div>
        <Space>
          <Button icon={<SyncOutlined spin={syncing} />} loading={syncing} onClick={syncFromSqlServer}>
            Sync SQL Server
          </Button>
          <Upload {...uploadProps}>
            <Button type="primary" icon={<UploadOutlined />}>
              Upload Invoice CSV/XLSX
            </Button>
          </Upload>
        </Space>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          placeholder="Search invoice source, carrier, service, status"
          value={batchSearchText}
          onChange={(event) => setBatchSearchText(event.target.value)}
          onSearch={(value) => setBatchSearch(value.trim())}
        />
      </div>
      {lastSyncSummary && <Alert className="compact-alert" type="success" showIcon message={lastSyncSummary} />}
      <Table<InvoiceReconciliationBatch>
        rowKey="id"
        loading={isFetching}
        dataSource={data}
        columns={[
          { title: 'Batch', dataIndex: 'id', width: 90 },
          { title: 'File', dataIndex: 'name' },
          { title: 'Invoice Source', dataIndex: 'invoice_source_name', width: 260, render: (value, record) => value || record.name || '-' },
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: (value, record) => value || record.carrier_code || '-' },
          { title: 'Service', dataIndex: 'carrier_service_name', width: 210, render: (value, record) => value || record.carrier_service_code || '-' },
          { title: 'Status', dataIndex: 'status', width: 130, render: (value) => <Tag color={value === 'COMPLETED' ? 'green' : 'blue'}>{value}</Tag> },
          { title: 'Rows', dataIndex: 'total_rows', width: 90 },
          { title: 'Matched', dataIndex: 'matched_rows', width: 100 },
          { title: 'Exceptions', dataIndex: 'exception_rows', width: 110 },
          {
            title: '',
            width: 180,
            render: (_, record) => (
              <Space>
                <Button size="small" icon={<EyeOutlined />} onClick={() => openBatch(record)}>
                  Review
                </Button>
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  loading={exportingBatchId === record.id}
                  onClick={() => downloadBatchExport(record, 'disputes')}
                >
                  Disputes
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        size="96vw"
        className="reconciliation-review-drawer"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? `Invoice batch #${selected.id} - ${selected.invoice_source_name || selected.name}` : ''}
        extra={
          selected ? (
            <Space>
              <Button size="small" icon={<DownloadOutlined />} loading={exportingBatchId === selected.id} onClick={() => downloadBatchExport(selected)}>
                Export Excel
              </Button>
              <Button size="small" icon={<DownloadOutlined />} loading={exportingBatchId === selected.id} onClick={() => downloadBatchExport(selected, 'disputes')}>
                Disputes
              </Button>
            </Space>
          ) : null
        }
      >
        {selected && (
          <>
            <div className="review-summary-strip">
              <span>Total {(summary?.total ?? selected.total_rows).toLocaleString()}</span>
              <span>Matched {(summary?.matched ?? selected.matched_rows).toLocaleString()}</span>
              <span>Exceptions {(summary?.exceptions ?? selected.exception_rows).toLocaleString()}</span>
              <span>Overcharge {(summary?.overcharge ?? 0).toLocaleString()}</span>
              <span>Undercharge {(summary?.undercharge ?? 0).toLocaleString()}</span>
              <span>Unmatched {(summary?.unmatched ?? 0).toLocaleString()}</span>
              <span>Actual inc GST {money(summary?.actual_total)}</span>
              <span>System Est {money(summary?.system_estimate_total)}</span>
              <span>Loaded {selectedItems.length.toLocaleString()} / {selectedTotal.toLocaleString()}</span>
            </div>

            <div className="reconciliation-control-panel">
              <Segmented<DataView> size="small" value={dataView} options={dataViewOptions} onChange={changeDataView} />
              <div className="reconciliation-filter-grid">
                <label className="reconciliation-filter-field">
                  <span>ERP Order</span>
                  <Input size="small" allowClear value={itemFilters.order_no} onChange={(event) => updateFilter('order_no', event.target.value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>Tracking</span>
                  <Input size="small" allowClear value={itemFilters.tracking} onChange={(event) => updateFilter('tracking', event.target.value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>Invoice No</span>
                  <Input size="small" allowClear value={itemFilters.invoice_no} onChange={(event) => updateFilter('invoice_no', event.target.value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>Carrier</span>
                  <Input size="small" allowClear value={itemFilters.carrier_name} onChange={(event) => updateFilter('carrier_name', event.target.value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>Invoice Source</span>
                  <Input size="small" allowClear value={itemFilters.invoice_source_name} onChange={(event) => updateFilter('invoice_source_name', event.target.value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>ERP Est</span>
                  <Select size="small" value={itemFilters.has_erp_estimate} options={estimateOptions} onChange={(value) => updateFilter('has_erp_estimate', value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>System Est</span>
                  <Select size="small" value={itemFilters.has_system_estimate} options={estimateOptions} onChange={(value) => updateFilter('has_system_estimate', value)} />
                </label>
                <label className="reconciliation-filter-field">
                  <span>Order Match</span>
                  <Select size="small" value={itemFilters.has_order} options={estimateOptions} onChange={(value) => updateFilter('has_order', value)} />
                </label>
              </div>
              <div className="reconciliation-column-controls">
                <Space size={6}>
                  <FilterOutlined />
                  <Typography.Text type="secondary">Columns</Typography.Text>
                </Space>
                <Checkbox.Group options={columnGroupOptions} value={visibleColumnGroups} onChange={(value) => setVisibleColumnGroups(value as ColumnGroup[])} />
                <Button size="small" onClick={clearItemFilters}>
                  Clear filters
                </Button>
              </div>
            </div>

            <div className="list-search-row">
              <Input.Search
                allowClear
                className="resource-search"
                size="small"
                placeholder="Search order, tracking, invoice, carrier, reason"
                value={itemSearchText}
                onChange={(event) => setItemSearchText(event.target.value)}
                onSearch={(value) => {
                  const nextSearch = value.trim()
                  setItemSearch(nextSearch)
                  void loadBatchItems(selected, 1, selectedPageSize, nextSearch, dataView, itemFilters)
                }}
              />
            </div>
            <Table<InvoiceReconciliationItem>
              rowKey="id"
              size="small"
              className="reconciliation-review-table"
              loading={loadingItems || summaryQuery.isFetching}
              dataSource={selectedItems}
              columns={itemColumns}
              tableLayout="fixed"
              expandable={{ expandedRowRender: (record) => <DetailDescriptions record={record} /> }}
              scroll={{ x: 2270, y: 'calc(100vh - 355px)' }}
              pagination={{
                current: selectedPage,
                pageSize: selectedPageSize,
                total: selectedTotal,
                showSizeChanger: true,
                pageSizeOptions: [50, 100, 200],
                size: 'small',
                showTotal: (total, range) => `${range[0]}-${range[1]} / ${total}`,
              }}
              rowClassName={(record) => `reconciliation-row reconciliation-row-${record.match_status.toLowerCase()}`}
              onChange={(pagination) => {
                void loadBatchItems(selected, pagination.current || 1, pagination.pageSize || selectedPageSize, itemSearch, dataView, itemFilters)
              }}
            />
          </>
        )}
      </Drawer>
    </section>
  )
}
