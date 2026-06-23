import { DownloadOutlined, EyeOutlined, SyncOutlined, UploadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Drawer, Input, Space, Table, Tag, Typography, Upload, message, type UploadProps } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, listResource, type Paginated } from '../api/client'
import type { InvoiceReconciliationBatch, InvoiceReconciliationItem } from '../types'

const money = (value?: string | null) => (value === null || value === undefined ? '-' : `$${Number(value).toFixed(2)}`)
const erpEstimateMoney = (value?: string | null) => {
  if (value === null || value === undefined) return '-'
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
const filenameFromDisposition = (value?: string) => {
  const match = value?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  return match ? decodeURIComponent(match[1]) : ''
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
  const { data = [], isFetching } = useQuery({
    queryKey: ['invoice-reconciliation-batches', batchSearch],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '200' })
      if (batchSearch) params.set('search', batchSearch)
      return listResource<InvoiceReconciliationBatch>(`/invoice-reconciliation-batches/?${params.toString()}`)
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

  const loadBatchItems = useCallback(async (batch: InvoiceReconciliationBatch, page = 1, pageSize = selectedPageSize, searchValue = itemSearch) => {
    setSelected(batch)
    setLoadingItems(true)
    try {
      const { data: payload } = await api.get<Paginated<InvoiceReconciliationItem> | InvoiceReconciliationItem[]>('/invoice-reconciliation-items/', {
        params: { batch: batch.id, page, page_size: pageSize, search: searchValue || undefined },
      })
      const rows = Array.isArray(payload) ? payload : payload.results
      setSelectedItems(rows)
      setSelectedTotal(Array.isArray(payload) ? rows.length : payload.count)
      setSelectedPage(page)
      setSelectedPageSize(pageSize)
    } finally {
      setLoadingItems(false)
    }
  }, [itemSearch, selectedPageSize])

  const openBatch = async (batch: InvoiceReconciliationBatch) => {
    setSelectedItems([])
    setSelectedTotal(0)
    setItemSearchText('')
    setItemSearch('')
    await loadBatchItems(batch, 1, selectedPageSize, '')
  }

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const nextSearch = itemSearchText.trim()
      setItemSearch(nextSearch)
      if (selected) void loadBatchItems(selected, 1, selectedPageSize, nextSearch)
    }, 350)
    return () => window.clearTimeout(handle)
  }, [itemSearchText, loadBatchItems, selected, selectedPageSize])

  const itemColumns = [
    { title: 'ERP Order', dataIndex: 'order_no', width: 170, ellipsis: true, render: compactText },
    { title: 'Tracking', dataIndex: 'consignment_no', width: 120, ellipsis: true, render: compactText },
    { title: 'Invoice', dataIndex: 'invoice_no', width: 118, ellipsis: true, render: compactText },
    { title: 'Source', dataIndex: 'invoice_source_name', width: 170, ellipsis: true, render: compactText },
    { title: 'Carrier', dataIndex: 'carrier_name', width: 140, ellipsis: true, render: (value: string, record: InvoiceReconciliationItem) => value || record.carrier_code || '-' },
    { title: 'Service', dataIndex: 'carrier_service_name', width: 150, ellipsis: true, render: (value: string, record: InvoiceReconciliationItem) => value || record.carrier_service_code || '-' },
    {
      title: 'ERP Est. inc GST',
      dataIndex: 'estimated_freight_inc_gst',
      width: 112,
      align: 'right' as const,
      className: 'money-cell',
      render: erpEstimateMoney,
    },
    { title: 'System Est.', dataIndex: 'system_estimated_freight', width: 92, align: 'right' as const, className: 'money-cell', render: money },
    { title: 'Actual', dataIndex: 'actual_freight', width: 86, align: 'right' as const, className: 'money-cell', render: money },
    { title: 'ERP Diff', dataIndex: 'variance_amount', width: 86, align: 'right' as const, className: 'money-cell', render: erpVarianceMoney },
    { title: 'ERP %', dataIndex: 'variance_percent', width: 74, align: 'right' as const, render: erpVariancePercent },
    { title: 'Sys Diff', dataIndex: 'system_variance_amount', width: 86, align: 'right' as const, className: 'money-cell', render: money },
    { title: 'Sys %', dataIndex: 'system_variance_percent', width: 72, align: 'right' as const, render: percent },
    { title: 'Status', dataIndex: 'match_status', width: 92, render: (value: string) => <Tag color={value === 'MATCHED' ? 'green' : value === 'EXCEPTION' ? 'red' : 'default'}>{value}</Tag> },
    { title: 'Type', dataIndex: 'variance_type', width: 104, render: (value: string) => <Tag color={value === 'OVERCHARGE' ? 'red' : value === 'UNDERCHARGE' ? 'gold' : value === 'OK' ? 'green' : 'default'}>{value}</Tag> },
    { title: 'Dispute', dataIndex: 'dispute_recommended', width: 70, render: (value: boolean) => <Tag color={value ? 'red' : 'default'}>{value ? 'Y' : 'N'}</Tag> },
    { title: 'System Reason', dataIndex: 'system_estimate_reason', width: 210, ellipsis: true, render: compactText },
    { title: 'Reason', dataIndex: 'reason', width: 360, ellipsis: true, render: compactText },
  ]

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

  return (
    <section className="page-surface">
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Invoice Reconciliation</Typography.Title>
          <Typography.Text type="secondary">Upload carrier invoices, match estimates to actual freight, and produce a dispute list.</Typography.Text>
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
              <span>Total {selected.total_rows.toLocaleString()}</span>
              <span>Matched {selected.matched_rows.toLocaleString()}</span>
              <span>Exceptions {selected.exception_rows.toLocaleString()}</span>
              <span>Loaded {selectedItems.length.toLocaleString()} / {selectedTotal.toLocaleString()}</span>
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
                  void loadBatchItems(selected, 1, selectedPageSize, nextSearch)
                }}
              />
            </div>
            <Table<InvoiceReconciliationItem>
              rowKey="id"
              size="small"
              className="reconciliation-review-table"
              loading={loadingItems}
              dataSource={selectedItems}
              columns={itemColumns}
              tableLayout="fixed"
              scroll={{ x: 2270, y: 'calc(100vh - 245px)' }}
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
                void loadBatchItems(selected, pagination.current || 1, pagination.pageSize || selectedPageSize, itemSearch)
              }}
            />
          </>
        )}
      </Drawer>
    </section>
  )
}
