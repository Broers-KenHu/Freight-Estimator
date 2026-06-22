import { CalculatorOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Drawer,
  Grid,
  Input,
  InputNumber,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  type TablePaginationConfig,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { api, type Paginated } from '../api/client'
import type { FreightAuditChargeLine, FreightAuditComponent, FreightAuditItem, FreightAuditResult, FreightAuditRow } from '../types'
import { nonZeroChargeLines } from '../utils/charges'

const carrierLabels: Record<string, string> = {
  hunter: 'Hunter',
  allied: 'Allied',
  eiz: 'EIZ',
  direct_freight: 'Direct Freight',
  orange_connex: 'Orange Connex',
}

const preferredCarrierOrder = ['hunter', 'allied', 'eiz', 'direct_freight', 'orange_connex']
type AuditMode = 'CONSIGNMENT' | 'ORDER' | 'ITEM'

const money = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `$${numeric.toFixed(2)}` : '-'
}

const compactMoney = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(2) : '-'
}

const erpEstimateValueIncGst = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric * 1.1 : null
}

const erpEstimateMoney = (value?: string | number | null) => {
  const incGst = erpEstimateValueIncGst(value)
  return incGst === null ? '-' : `$${incGst.toFixed(2)}`
}

const compactNumber = (value?: string | number | null, digits = 2) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '-'
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

const carrierLabel = (key: string) =>
  carrierLabels[key] || key.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ')

const varianceColor = (value?: string | number | null) => {
  const numeric = Number(value || 0)
  if (!Number.isFinite(numeric) || numeric === 0) return undefined
  return numeric > 0 ? '#b42318' : '#067647'
}

const sortTracking = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })

type SummaryItem = {
  label: string
  value: string
  rawValue?: string | number | null
  wide?: boolean
}

const trackingFromLine = (line: FreightAuditChargeLine, knownTrackings: string[]) => {
  if (line.tracking) return line.tracking
  const separator = ' / '
  const index = line.description.indexOf(separator)
  if (index <= 0) return ''
  const prefix = line.description.slice(0, index)
  return knownTrackings.includes(prefix) ? prefix : ''
}

const descriptionWithoutTracking = (line: FreightAuditChargeLine, tracking: string) => {
  const prefix = `${tracking} / `
  return tracking && line.description.startsWith(prefix) ? line.description.slice(prefix.length) : line.description
}

const debugEntries = (debug?: Record<string, unknown>, limit = 18) =>
  Object.entries(debug || {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, limit)

function ResultAmount({ result, erpEstimate }: { result?: FreightAuditResult; erpEstimate?: string | null }) {
  if (!result) return <Typography.Text type="secondary">-</Typography.Text>
  if (result.availability !== 'AVAILABLE') {
    return (
      <Tooltip title={result.not_available_reason || 'Not available'}>
        <Tag color="default">N/A</Tag>
      </Tooltip>
    )
  }
  const erpEstimateIncGst = erpEstimateValueIncGst(erpEstimate)
  const total = Number(result.total_inc_gst)
  const erpVariance = Number.isFinite(total) && erpEstimateIncGst !== null ? total - erpEstimateIncGst : result.variance_to_erp
  return (
    <div className="audit-carrier-cell">
      <Typography.Text strong>{money(result.total_inc_gst)}</Typography.Text>
      <Typography.Text style={{ color: varianceColor(erpVariance) }}>ERP {compactMoney(erpVariance)}</Typography.Text>
      <Typography.Text style={{ color: varianceColor(result.variance_to_invoice) }}>INV {compactMoney(result.variance_to_invoice)}</Typography.Text>
    </div>
  )
}

function DebugGrid({ debug, limit = 18 }: { debug?: Record<string, unknown>; limit?: number }) {
  const rows = debugEntries(debug, limit)
  if (!rows.length) return null
  return (
    <div className="audit-debug-grid">
      {rows.map(([key, value]) => (
        <span key={key}>
          <b>{key}</b>
          <em>{String(value)}</em>
        </span>
      ))}
    </div>
  )
}

function ItemsUsedTable({
  items,
  itemColumns,
}: {
  items?: FreightAuditItem[]
  itemColumns: ColumnsType<FreightAuditItem>
}) {
  if (!items?.length) return null
  return (
    <div className="audit-items-panel">
      <Typography.Text strong>Items used for calculation</Typography.Text>
      <Table<FreightAuditItem>
        rowKey={(item, index) => `${item.combo_parent_sku || ''}-${item.sku}-${index}`}
        size="small"
        className="audit-items-table"
        columns={itemColumns}
        dataSource={items}
        pagination={false}
        tableLayout="fixed"
        scroll={{ x: 860 }}
      />
    </div>
  )
}

function TrackingBreakdown({
  component,
  itemColumns,
  lines,
  lineColumns,
}: {
  component: FreightAuditComponent
  itemColumns: ColumnsType<FreightAuditItem>
  lines: FreightAuditChargeLine[]
  lineColumns: ColumnsType<FreightAuditChargeLine>
}) {
  return (
    <section className="audit-tracking-block">
      <div className="audit-tracking-head">
        <div>
          <Typography.Text type="secondary">Tracking</Typography.Text>
          <Typography.Text strong>{component.tracking || '-'}</Typography.Text>
        </div>
        <div>
          <Typography.Text type="secondary">Total</Typography.Text>
          <Typography.Text strong>{money(component.total_inc_gst)}</Typography.Text>
        </div>
        <Tag color={component.availability === 'AVAILABLE' ? 'green' : 'default'}>{component.availability || 'UNKNOWN'}</Tag>
        {component.not_available_reason && <Typography.Text type="secondary">{component.not_available_reason}</Typography.Text>}
      </div>
      <DebugGrid debug={component.debug_breakdown} limit={12} />
      <ItemsUsedTable items={component.items} itemColumns={itemColumns} />
      {lines.length > 0 && (
        <Table<FreightAuditChargeLine>
          rowKey={(line, index) => `${component.tracking}-${line.type}-${line.description}-${index}`}
          size="small"
          className="audit-lines-table"
          columns={lineColumns}
          dataSource={lines}
          pagination={false}
          tableLayout="fixed"
          scroll={{ x: 780 }}
        />
      )}
    </section>
  )
}

function ResultCard({ result }: { result: FreightAuditResult }) {
  const lines = nonZeroChargeLines(result.raw_payload?.charge_lines)
  const components = [...(result.raw_payload?.components || [])].sort((a, b) => sortTracking(a.tracking || '', b.tracking || ''))
  const knownTrackings = components.map((component) => component.tracking).filter(Boolean)
  const debug = result.raw_payload?.debug_breakdown || {}
  const summaryItems: SummaryItem[] = result.raw_payload?.rate_card
    ? [{ label: 'Rate Card', value: result.raw_payload.rate_card, wide: true }]
    : []

  const lineColumns: ColumnsType<FreightAuditChargeLine> = [
    { title: 'Tracking', dataIndex: 'tracking', width: 118, render: (value) => value || '-' },
    { title: 'Type', dataIndex: 'type', width: 92 },
    { title: 'Description', dataIndex: 'description', width: 320, className: 'audit-wrap-cell' },
    { title: 'Ex GST', dataIndex: 'amount_ex_gst', width: 86, align: 'right', render: money },
    { title: 'GST', dataIndex: 'gst_amount', width: 76, align: 'right', render: money },
    { title: 'Inc GST', dataIndex: 'amount_inc_gst', width: 86, align: 'right', render: money },
  ]
  const itemColumns: ColumnsType<FreightAuditItem> = [
    {
      title: 'SKU',
      dataIndex: 'sku',
      width: 220,
      className: 'audit-wrap-cell',
      render: (value, item) => (
        <div className="audit-item-sku">
          <Typography.Text strong>{value || '-'}</Typography.Text>
          {(item.category || item.combo_parent_sku) && (
            <Typography.Text type="secondary">
              {[item.category, item.combo_parent_sku ? `combo ${item.combo_parent_sku}` : ''].filter(Boolean).join(' / ')}
            </Typography.Text>
          )}
        </div>
      ),
    },
    { title: 'Qty', dataIndex: 'qty', width: 64, align: 'right', render: (value) => compactNumber(value, 3) },
    { title: 'Unit kg', dataIndex: 'unit_weight_kg', width: 76, align: 'right', render: (value) => compactNumber(value, 3) },
    { title: 'Actual kg', dataIndex: 'actual_kg', width: 82, align: 'right', render: (value) => compactNumber(value, 2) },
    {
      title: 'L/W/H cm',
      width: 132,
      render: (_, item) => [item.length_cm, item.width_cm, item.height_cm].map((value) => compactNumber(value, 2)).join(' / '),
    },
    { title: 'Cubic kg', dataIndex: 'cubic_kg', width: 82, align: 'right', render: (value) => compactNumber(value, 2) },
    { title: 'Factor', dataIndex: 'cubic_factor', width: 64, align: 'right', render: (value) => compactNumber(value, 0) },
    { title: 'Source', dataIndex: 'calculation_source', width: 126, className: 'audit-wrap-cell', render: (value) => value || '-' },
  ]
  const linesByTracking = lines.reduce<Record<string, FreightAuditChargeLine[]>>((grouped, line) => {
    const tracking = trackingFromLine(line, knownTrackings)
    if (!tracking) return grouped
    grouped[tracking] ||= []
    grouped[tracking].push({ ...line, tracking, description: descriptionWithoutTracking(line, tracking) })
    return grouped
  }, {})
  const normalizedLines = lines
    .map((line) => {
      const tracking = trackingFromLine(line, knownTrackings)
      return { ...line, tracking, description: descriptionWithoutTracking(line, tracking) }
    })
    .sort((a, b) => sortTracking(a.tracking || '', b.tracking || '') || a.type.localeCompare(b.type))

  return (
    <Card
      size="small"
      className="audit-result-card"
      title={
        <Space size={8} wrap>
          <Typography.Text strong>{result.carrier_name || carrierLabel(result.carrier_key)}</Typography.Text>
          {result.service_name && <Tag>{result.service_name}</Tag>}
          {result.quote_channel_code && <Tag color="blue">{result.quote_channel_code}</Tag>}
          <Tag color={result.availability === 'AVAILABLE' ? 'green' : 'default'}>{result.availability}</Tag>
        </Space>
      }
      extra={<Typography.Text strong>{result.availability === 'AVAILABLE' ? money(result.total_inc_gst) : result.not_available_reason || 'N/A'}</Typography.Text>}
    >
      {summaryItems.length > 0 && (
        <div className="audit-result-summary" aria-label="Rate card summary">
          {summaryItems.map((item) => (
            <span key={item.label} className={`audit-result-summary-item${item.wide ? ' audit-result-summary-item-wide' : ''}`}>
              <span className="audit-result-summary-label">{item.label}</span>
              <span className="audit-result-summary-value">{item.value}</span>
            </span>
          ))}
        </div>
      )}
      <DebugGrid debug={debug} />
      {components.length > 0 ? (
        <div className="audit-tracking-stack">
          {components.map((component) => (
            <TrackingBreakdown
              key={component.tracking || component.quote_candidate_id}
              component={component}
              itemColumns={itemColumns}
              lines={linesByTracking[component.tracking] || []}
              lineColumns={lineColumns}
            />
          ))}
        </div>
      ) : (
        <>
          <ItemsUsedTable items={result.raw_payload?.items} itemColumns={itemColumns} />
          {normalizedLines.length > 0 && (
            <Table<FreightAuditChargeLine>
              rowKey={(line, index) => `${line.tracking || 'single'}-${line.type}-${line.description}-${index}`}
              size="small"
              className="audit-lines-table"
              columns={lineColumns}
              dataSource={normalizedLines}
              pagination={false}
              tableLayout="fixed"
              scroll={{ x: 780 }}
            />
          )}
        </>
      )}
    </Card>
  )
}

export function FreightAuditMatrix() {
  const queryClient = useQueryClient()
  const screens = Grid.useBreakpoint()
  const [messageApi, contextHolder] = message.useMessage()
  const [mode, setMode] = useState<AuditMode>('CONSIGNMENT')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [batchId, setBatchId] = useState<number | null>(221)
  const [limit, setLimit] = useState<number | null>(5000)
  const [selected, setSelected] = useState<FreightAuditRow | null>(null)

  const { data, isFetching } = useQuery({
    queryKey: ['freight-audit-rows', mode, search, page, pageSize],
    queryFn: async () =>
      (
        await api.get<Paginated<FreightAuditRow>>('/freight-audit-rows/', {
          params: {
            calculation_mode: mode,
            search: search || undefined,
            page,
            page_size: pageSize,
          },
        })
      ).data,
  })

  const rows = useMemo(() => data?.results || [], [data?.results])
  const total = data?.count || 0
  const carrierKeys = useMemo(() => {
    const discovered = new Set<string>(preferredCarrierOrder)
    rows.forEach((row) => {
      Object.keys(row.best_results || {}).forEach((key) => {
        if (key) discovered.add(key)
      })
    })
    return [
      ...preferredCarrierOrder.filter((key) => discovered.has(key)),
      ...Array.from(discovered).filter((key) => !preferredCarrierOrder.includes(key)).sort(),
    ]
  }, [rows])

  const buildMutation = useMutation({
    mutationFn: async () =>
      (
        await api.post('/freight-audit-rows/build-from-reconciliation/', {
          batch_id: batchId || undefined,
          source_config: 'HUNTER',
          mode,
          limit: limit || undefined,
          order_batch_size: 5000,
        })
      ).data,
    onSuccess: (payload) => {
      messageApi.success('Freight audit matrix build completed')
      if (payload?.output) console.info(payload.output)
      queryClient.invalidateQueries({ queryKey: ['freight-audit-rows'] })
    },
    onError: () => messageApi.error('Freight audit matrix build failed'),
  })

  const columns: ColumnsType<FreightAuditRow> = [
    { title: 'Order No', dataIndex: 'order_no', width: 190, fixed: 'left', className: 'audit-wrap-cell' },
    { title: 'Tracking', dataIndex: 'tracking_no', width: 230, fixed: 'left', className: 'audit-wrap-cell', render: (value) => value || '-' },
    { title: 'Mode', dataIndex: 'calculation_mode', width: 110, render: (value) => <Tag>{value}</Tag> },
    { title: 'Platform', dataIndex: 'platform_name', width: 180, className: 'audit-wrap-cell', render: (value, record) => value || record.platform_code || '-' },
    { title: 'Warehouse', dataIndex: 'warehouse_code', width: 96, render: (value) => value || '-' },
    { title: 'Destination', width: 230, className: 'audit-wrap-cell', render: (_, record) => [record.suburb, record.state, record.postcode].filter(Boolean).join(', ') || '-' },
    { title: 'Items', dataIndex: 'item_count', width: 72, align: 'right' },
    { title: 'ERP Est. inc GST', dataIndex: 'erp_estimated_freight', width: 126, align: 'right', render: erpEstimateMoney },
    { title: 'Actual', dataIndex: 'invoice_actual_freight', width: 96, align: 'right', render: money },
    ...carrierKeys.map((key) => ({
      title: carrierLabel(key),
      dataIndex: ['best_results', key],
      width: 126,
      align: 'right' as const,
      render: (_: unknown, record: FreightAuditRow) => <ResultAmount result={record.best_results?.[key]} erpEstimate={record.erp_estimated_freight} />,
    })),
    { title: 'Status', dataIndex: 'status', width: 112, render: (value) => <Tag color={value === 'COMPLETED' ? 'green' : value === 'FAILED' ? 'red' : 'default'}>{value}</Tag> },
  ]

  const onTableChange = (pagination: TablePaginationConfig) => {
    setPage(pagination.current || 1)
    setPageSize(pagination.pageSize || pageSize)
  }

  const sortedResults = [...(selected?.results || [])].sort((a, b) => {
    const carrierCompare = carrierLabel(a.carrier_key).localeCompare(carrierLabel(b.carrier_key))
    if (carrierCompare) return carrierCompare
    if (a.availability !== b.availability) return a.availability === 'AVAILABLE' ? -1 : 1
    return Number(a.total_inc_gst || 999999) - Number(b.total_inc_gst || 999999)
  })

  return (
    <section className="page-surface freight-audit-page">
      {contextHolder}
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Freight Audit Matrix</Typography.Title>
          <Typography.Text type="secondary">Compare ERP estimate, invoice actual, and every enabled CourieDelivery carrier calculator for historical orders.</Typography.Text>
        </div>
        <Space className="page-actions" wrap>
          <Segmented
            value={mode}
            options={[
              { label: 'Consignment', value: 'CONSIGNMENT' },
              { label: 'Order', value: 'ORDER' },
              { label: 'Single Item', value: 'ITEM' },
            ]}
            onChange={(value) => {
              setMode(value as AuditMode)
              setPage(1)
            }}
          />
          <InputNumber aria-label="Batch ID" min={1} value={batchId} onChange={(value) => setBatchId(value)} placeholder="Batch" />
          <InputNumber aria-label="Order limit" min={1} value={limit} onChange={(value) => setLimit(value)} placeholder="All orders" />
          <Button icon={<CalculatorOutlined />} loading={buildMutation.isPending} onClick={() => buildMutation.mutate()}>
            Build Orders
          </Button>
        </Space>
      </div>
      <div className="audit-filter-bar">
        <Input
          allowClear
          className="resource-search"
          prefix={<SearchOutlined />}
          placeholder="Search order, tracking, platform, suburb, postcode"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setPage(1)
          }}
        />
        <Button icon={<ReloadOutlined />} onClick={() => queryClient.invalidateQueries({ queryKey: ['freight-audit-rows'] })}>
          Refresh
        </Button>
        <Typography.Text type="secondary">{total.toLocaleString()} rows</Typography.Text>
      </div>
      <Table<FreightAuditRow>
        rowKey="id"
        size="small"
        className="freight-audit-table"
        loading={isFetching}
        columns={columns}
        dataSource={rows}
        tableLayout="fixed"
        scroll={{ x: 1580 + carrierKeys.length * 126, y: 'calc(100vh - 280px)' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [50, 100, 200],
          showTotal: (count, range) => `${range[0]}-${range[1]} / ${count}`,
        }}
        onChange={onTableChange}
        onRow={(record) => ({
          onClick: () => setSelected(record),
        })}
      />
      <Drawer
        width={screens.md ? 'min(1500px, 96vw)' : '100vw'}
        className="freight-audit-drawer"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? `Freight audit - ${selected.order_no}` : ''}
      >
        {selected && (
          <>
            <div className="audit-detail-header">
              <span>ERP Est. inc GST <b>{erpEstimateMoney(selected.erp_estimated_freight)}</b></span>
              <span>Invoice Actual <b>{money(selected.invoice_actual_freight)}</b></span>
              <span>Tracking <b>{selected.tracking_no || '-'}</b></span>
              <span>Mode <b>{selected.calculation_mode}</b></span>
              <span>Destination <b>{[selected.suburb, selected.state, selected.postcode].filter(Boolean).join(', ') || '-'}</b></span>
              <span>Items <b>{selected.item_count}</b></span>
            </div>
            <div className="audit-result-stack">
              {sortedResults.map((result) => (
                <ResultCard key={result.id} result={result} />
              ))}
            </div>
          </>
        )}
      </Drawer>
    </section>
  )
}
