import { DeleteOutlined, EyeOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  AutoComplete,
  Popover,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { api, listResource, unpackList, type Paginated } from '../api/client'
import type { Platform, QuoteCandidate, QuoteRun, Warehouse } from '../types'
import { nonZeroChargeLines } from '../utils/charges'

const money = (value?: string | null) => `$${Number(value || 0).toFixed(2)}`
const optionalMoney = (value?: string | number | null) => (value === undefined || value === null || value === '' ? '-' : `$${Number(value || 0).toFixed(2)}`)
const erpEstimateMoney = (value?: string | number | null) => {
  if (value === undefined || value === null || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `$${(numeric * 1.1).toFixed(2)}` : '-'
}
type SkuField = { key: number; name: number }
type QuoteInputMode = 'SKU_LOOKUP' | 'MANUAL_DIMENSIONS' | 'ORDER_LOOKUP'
type SkuPickerMode = 'multiple' | 'single'

type DestinationOption = {
  suburb: string
  state: string
  postcode: string
  label: string
  rate_card_count?: number
}

type ManualQuoteItem = {
  sku?: string
  sku_input?: string
  qty?: number | string
  unit_weight_kg?: number | string
  length_cm?: number | string
  width_cm?: number | string
  height_cm?: number | string
  sku_type?: 'SKU' | 'COMBO'
  combo_component_count?: number
  sku_description?: string
  source?: string
  tracking_numbers?: string[]
  sku_master_found?: boolean
  category?: string
  source_rows?: number
}

type ManualQuoteFormValues = {
  platform_code: string
  warehouse_code: string
  destination_search?: string
  destination: Record<string, string>
  quote_mode?: string
  quote_input_mode: QuoteInputMode
  items: ManualQuoteItem[]
  options?: Record<string, unknown>
}

type SkuLookupSku = {
  sku: string
  description: string
  category?: string
  unit_weight_kg: string
  length_cm: string
  width_cm: string
  height_cm: string
  active: boolean
  is_combo: boolean
  combo_type_label?: string
  combo_component_count: number
}

type SkuLookupComponent = {
  combo_sku: string
  component_sku: string
  component_qty: string
  combo_title: string
  component_sku_snapshot?: SkuLookupSku | null
}

type SkuLookupResponse = {
  sku: SkuLookupSku
  components: SkuLookupComponent[]
}

type OrderLookupLine = {
  source?: string
  sku: string
  description?: string
  qty?: string
  unit_weight_kg?: string
  length_cm?: string
  width_cm?: string
  height_cm?: string
  tracking_no?: string
  tracking_numbers?: string[]
  carrier_name?: string
  carrier_channel?: string
  service_provider?: string
  warehouse_code?: string
  warehouse_owner_code?: string
  sku_type?: 'SKU' | 'COMBO'
  combo_component_count?: number
  sku_description?: string
  sku_master_found?: boolean
  category?: string
  source_rows?: number
}

type OrderLookupResult = {
  id: number
  label: string
  order_no: string
  order_date?: string | null
  order_refs: Record<string, string>
  platform_code: string
  platform_name?: string
  platform_raw_code?: string
  platform_source?: string
  warehouse_code: string
  warehouse_name?: string
  warehouse_raw_code?: string
  warehouse_source?: string
  tracking_numbers?: string[]
  shipping_option?: string
  source_order_type?: string
  source_estimated_freight?: string
  postage_shipping_estimated_amount?: string
  actual_carrier?: string
  destination: { state: string; suburb: string; postcode: string; country: string }
  destination_label: string
  sales_items: OrderLookupLine[]
  shipment_items: OrderLookupLine[]
  quote_items: OrderLookupLine[]
  quote_item_source: 'shipment' | 'sales'
  lsp_quote?: LspOrderQuote | null
}

type LspOrderQuoteOption = {
  id: number
  option_index: number
  agent_name?: string
  agent_code?: string
  carrier_name?: string
  carrier_code?: string
  service_name?: string
  service_code?: string
  can_shipping: boolean
  shipping_cost?: string
  carrier_shipping_cost?: string
  calc_mode?: string
  remark?: string
}

type LspOrderQuoteAgentBreakdown = {
  agent_name?: string
  agent_code?: string
  carrier_name?: string
  carrier_code?: string
  service_name?: string
  service_code?: string
  shipment_count?: number
  selected_price?: string
  carrier_cost?: string
}

type LspOrderQuoteBreakdownLine = {
  snapshot_id: number
  agent_name?: string
  carrier_name?: string
  service_name?: string
  tracking_no?: string
  shipment_code?: string
  line_type?: string
  description?: string
  amount?: string
}

type LspOrderQuoteSnapshot = {
  id: number
  lsp_reference_no?: string
  lsp_shipment_code?: string
  booking_tracking_no?: string
  quote_at?: string
  request_id?: string
  quote_id?: string
  warehouse_code?: string
  agent_name?: string
  agent_code?: string
  predicted_carrier_name?: string
  predicted_carrier_code?: string
  predicted_service_name?: string
  predicted_service_code?: string
  selected_price?: string
  selected_carrier_cost?: string
  match_reason?: string
  option_count?: number
  internal_log_item_count?: number
  options?: LspOrderQuoteOption[]
  log_items?: LspQuoteTaskLogItem[]
}

type LspOrderQuote = {
  id: number
  lsp_reference_no?: string
  lsp_shipment_code?: string
  quote_at?: string
  request_id?: string
  quote_id?: string
  warehouse_code?: string
  predicted_carrier_name?: string
  predicted_carrier_code?: string
  predicted_service_name?: string
  predicted_service_code?: string
  agent_name?: string
  agent_code?: string
  selected_price?: string
  selected_carrier_cost?: string
  total_selected_price?: string
  total_carrier_cost?: string
  erp_estimated_freight?: string
  match_reason?: string
  option_count?: number
  internal_log_item_count?: number
  snapshot_count?: number
  agent_breakdown?: LspOrderQuoteAgentBreakdown[]
  breakdown_lines?: LspOrderQuoteBreakdownLine[]
  snapshots?: LspOrderQuoteSnapshot[]
  options: LspOrderQuoteOption[]
}

type LspQuoteTaskLogItem = {
  id: number
  log_action?: string
  item_scope?: string
  agent_name?: string
  agent_code?: string
  carrier_agent_code?: string
  carrier_code?: string
  channel_code?: string
  service_level?: string
  can_shipping: boolean
  shipping_cost?: string | null
  shipping_cost_with_tax?: string | null
  surcharge?: string | null
  failed_reason?: string
}

const cleanQuotePayload = (values: ManualQuoteFormValues) => {
  const payload = { ...values }
  delete payload.destination_search
  return {
    ...payload,
    items: (values.items || []).map((item) => ({
      sku: item.sku || '',
      sku_input: item.sku_input || item.sku || '',
      qty: item.qty,
      unit_weight_kg: item.unit_weight_kg,
      length_cm: item.length_cm,
      width_cm: item.width_cm,
      height_cm: item.height_cm,
      sku_type: item.sku_type,
      combo_component_count: item.combo_component_count || 0,
      sku_description: item.sku_description || '',
      source: item.source || '',
      tracking_numbers: item.tracking_numbers || [],
      sku_master_found: item.sku_master_found,
      category: item.category || '',
      source_rows: item.source_rows || 0,
    })),
  }
}

const toNumber = (value?: string | number | null) => Number(value || 0)

const comboDisplayDimensions = (components: SkuLookupComponent[], fallback: SkuLookupSku) => {
  const totalWeight = components.reduce(
    (total, component) => total + toNumber(component.component_sku_snapshot?.unit_weight_kg) * toNumber(component.component_qty),
    0,
  )
  const maxLength = Math.max(0, ...components.map((component) => toNumber(component.component_sku_snapshot?.length_cm)))
  const maxWidth = Math.max(0, ...components.map((component) => toNumber(component.component_sku_snapshot?.width_cm)))
  const maxHeight = Math.max(0, ...components.map((component) => toNumber(component.component_sku_snapshot?.height_cm)))
  return {
    unit_weight_kg: totalWeight || toNumber(fallback.unit_weight_kg),
    length_cm: maxLength || toNumber(fallback.length_cm),
    width_cm: maxWidth || toNumber(fallback.width_cm),
    height_cm: maxHeight || toNumber(fallback.height_cm),
  }
}

const itemFromSkuLookup = (data: SkuLookupResponse): ManualQuoteItem => {
  const displayDimensions = data.sku.is_combo
    ? comboDisplayDimensions(data.components, data.sku)
    : {
        unit_weight_kg: data.sku.unit_weight_kg,
        length_cm: data.sku.length_cm,
        width_cm: data.sku.width_cm,
        height_cm: data.sku.height_cm,
      }
  return {
    sku: data.sku.sku,
    sku_input: data.sku.sku,
    qty: 1,
    unit_weight_kg: displayDimensions.unit_weight_kg,
    length_cm: displayDimensions.length_cm,
    width_cm: displayDimensions.width_cm,
    height_cm: displayDimensions.height_cm,
    sku_type: data.sku.is_combo ? 'COMBO' : 'SKU',
    combo_component_count: data.components.length,
    sku_description: data.sku.description,
  }
}

const warehouseLabel = (warehouse: Warehouse) => {
  const name = (warehouse.name || '').trim()
  return !name || name.toUpperCase() === warehouse.code.toUpperCase() ? warehouse.code : `${warehouse.code} - ${name}`
}

const skuOptionCategory = (sku: SkuLookupSku) => {
  const category = (sku.category || '').trim()
  if (category) return category
  return sku.combo_type_label || (sku.is_combo ? 'Combo' : 'SKU')
}

const skuAutocompletePopupWidth = 520

const orderPlatformDisplay = (order: OrderLookupResult) =>
  order.platform_name || order.platform_raw_code || order.platform_code || 'All platforms'

const orderWarehouseDisplay = (order: OrderLookupResult) =>
  order.warehouse_name || order.warehouse_raw_code || order.warehouse_code || 'All warehouses'

const uniqueTextValues = (values: Array<string | undefined | null>) =>
  Array.from(new Set(values.map((value) => String(value || '').trim()).filter(Boolean)))

const orderRefValue = (order: OrderLookupResult, key: string) => String(order.order_refs?.[key] || '').trim()

const orderErpOrderDisplay = (order: OrderLookupResult) =>
  orderRefValue(order, 'erp_order_no') || order.order_no || order.label || '-'

const orderPlatformOrderValues = (order: OrderLookupResult) =>
  uniqueTextValues([orderRefValue(order, 'platform_order_no')])

const orderPlatformOrderDisplay = (order: OrderLookupResult) => orderPlatformOrderValues(order).join(', ') || '-'

const orderTrackingValues = (order: OrderLookupResult) => uniqueTextValues(order.tracking_numbers || [])

const orderTrackingDisplay = (order: OrderLookupResult) => orderTrackingValues(order).join(', ') || '-'

const sourceLabel = (source?: string) => {
  if (!source) return ''
  if (source === 'unmapped') return 'Unmapped'
  if (source.includes('erp_shipment_snapshot')) return 'ERP snapshot'
  if (source.includes('raw_payload')) return 'Raw payload'
  if (source.includes('historical_order_shipment')) return 'Shipment'
  if (source.includes('historical_order')) return 'Order'
  return source
}

function OrderLspQuoteCollapse({ quote }: { quote?: LspOrderQuote | null }) {
  const includedLogItems = (quote?.snapshots || []).flatMap((snapshot) => snapshot.log_items || [])
  const logsQuery = useQuery({
    queryKey: ['manual-order-lsp-logs', quote?.id],
    queryFn: async () => {
      const params = new URLSearchParams({ snapshot: String(quote?.id), page_size: '120' })
      const { data } = await api.get<Paginated<LspQuoteTaskLogItem>>(`/lsp-quote-log-items/?${params.toString()}`)
      return data
    },
    enabled: Boolean(quote?.id && quote.internal_log_item_count && !includedLogItems.length),
  })

  if (!quote) {
    return <Typography.Text className="order-lsp-empty" type="secondary">No matched LSP historical quote for this order.</Typography.Text>
  }

  const selectedCarrier = quote.predicted_carrier_name || quote.predicted_carrier_code || '-'
  const selectedService = quote.predicted_service_name || quote.predicted_service_code || '-'
  const selectedAgent = quote.agent_name || quote.agent_code || 'Unknown'
  const totalPrice = quote.total_selected_price || quote.selected_price
  const flattenedOptions = (quote.snapshots?.length ? quote.snapshots : [{ id: quote.id, options: quote.options } as LspOrderQuoteSnapshot])
    .flatMap((snapshot) =>
      (snapshot.options || []).map((option) => ({
        ...option,
        snapshot_id: snapshot.id,
        snapshot_label: snapshot.booking_tracking_no || snapshot.lsp_shipment_code || snapshot.lsp_reference_no || String(snapshot.id),
      })),
    )
  const logRows = includedLogItems.length ? includedLogItems : logsQuery.data?.results || []

  return (
    <Collapse
      size="small"
      className="order-lsp-quote-collapse"
      items={[
        {
          key: 'lsp',
          label: (
            <div className="order-lsp-quote-label">
              <Space size={8} wrap>
                <Typography.Text strong>LSP API historical total</Typography.Text>
                <Typography.Text strong>{optionalMoney(totalPrice)}</Typography.Text>
                <Tag color="blue">{selectedAgent}</Tag>
                <Typography.Text type="secondary">
                  {selectedCarrier} / {selectedService}
                </Typography.Text>
                {quote.match_reason && <Tag>{quote.match_reason}</Tag>}
              </Space>
              <Typography.Text type="secondary">
                {quote.snapshot_count || quote.snapshots?.length || 1} quote(s) / {quote.option_count || flattenedOptions.length || 0} option(s)
              </Typography.Text>
              <Typography.Text type="secondary" style={{ display: 'none' }}>
                {quote.lsp_reference_no || quote.lsp_shipment_code || '-'} · {quote.option_count || quote.options?.length || 0} options
              </Typography.Text>
            </div>
          ),
          children: (
            <Space direction="vertical" size="middle" className="full-width">
              <Descriptions bordered size="small" column={4}>
                <Descriptions.Item label="LSP Ref">{quote.lsp_reference_no || '-'}</Descriptions.Item>
                <Descriptions.Item label="Shipment">{quote.lsp_shipment_code || '-'}</Descriptions.Item>
                <Descriptions.Item label="Quote At">{quote.quote_at || '-'}</Descriptions.Item>
                <Descriptions.Item label="Warehouse">{quote.warehouse_code || '-'}</Descriptions.Item>
                <Descriptions.Item label="Agent">{selectedAgent}</Descriptions.Item>
                <Descriptions.Item label="Selected Carrier">{selectedCarrier}</Descriptions.Item>
                <Descriptions.Item label="Selected Service">{selectedService}</Descriptions.Item>
                <Descriptions.Item label="API Total">{optionalMoney(totalPrice)}</Descriptions.Item>
                <Descriptions.Item label="Carrier Cost">{optionalMoney(quote.total_carrier_cost || quote.selected_carrier_cost)}</Descriptions.Item>
              </Descriptions>

              <div>
                <Typography.Title level={5}>Agent breakdown</Typography.Title>
                <Table<LspOrderQuoteAgentBreakdown>
                  rowKey={(item) => `${item.agent_code || item.agent_name}-${item.carrier_code || item.carrier_name}-${item.service_code || item.service_name}`}
                  size="small"
                  pagination={false}
                  dataSource={quote.agent_breakdown || []}
                  scroll={{ x: 860 }}
                  columns={[
                    { title: 'Agent', width: 130, render: (_, item) => item.agent_name || item.agent_code || 'Unknown' },
                    { title: 'Carrier', width: 190, render: (_, item) => item.carrier_name || item.carrier_code || '-' },
                    { title: 'Service', width: 180, render: (_, item) => item.service_name || item.service_code || '-' },
                    { title: 'Quotes', dataIndex: 'shipment_count', width: 80, align: 'right' },
                    { title: 'Total', dataIndex: 'selected_price', width: 120, align: 'right', render: optionalMoney },
                    { title: 'Carrier Cost', dataIndex: 'carrier_cost', width: 120, align: 'right', render: optionalMoney },
                  ]}
                />
              </div>

              <div>
                <Typography.Title level={5}>Historical API quote breakdown</Typography.Title>
                <Table<LspOrderQuoteBreakdownLine>
                  rowKey={(item) => `${item.snapshot_id}-${item.line_type}-${item.tracking_no || item.shipment_code}`}
                  size="small"
                  pagination={false}
                  dataSource={quote.breakdown_lines || []}
                  scroll={{ x: 980 }}
                  columns={[
                    { title: 'Agent', width: 130, render: (_, item) => item.agent_name || 'Unknown' },
                    { title: 'Carrier', dataIndex: 'carrier_name', width: 190, render: (value) => value || '-' },
                    { title: 'Service', dataIndex: 'service_name', width: 180, render: (value) => value || '-' },
                    { title: 'Tracking', dataIndex: 'tracking_no', width: 180, render: (value) => value || '-' },
                    { title: 'Shipment', dataIndex: 'shipment_code', width: 180, render: (value) => value || '-' },
                    { title: 'Description', dataIndex: 'description', width: 220, render: (value) => value || '-' },
                    { title: 'Amount', dataIndex: 'amount', width: 110, align: 'right', render: optionalMoney },
                  ]}
                />
              </div>

              <Table<LspOrderQuoteOption & { snapshot_id?: number; snapshot_label?: string }>
                rowKey={(item) => `${item.snapshot_id || quote.id}-${item.id}`}
                size="small"
                pagination={false}
                dataSource={flattenedOptions}
                scroll={{ x: 1080 }}
                columns={[
                  { title: '#', dataIndex: 'option_index', width: 56 },
                  { title: 'Agent', width: 120, render: (_, item) => item.agent_name || item.agent_code || 'Unknown' },
                  { title: 'Carrier', width: 190, render: (_, item) => item.carrier_name || item.carrier_code || '-' },
                  { title: 'Service', width: 190, render: (_, item) => item.service_name || item.service_code || '-' },
                  { title: 'Can Ship', dataIndex: 'can_shipping', width: 90, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
                  { title: 'Shipping Cost', dataIndex: 'shipping_cost', width: 120, align: 'right', render: optionalMoney },
                  { title: 'Carrier Cost', dataIndex: 'carrier_shipping_cost', width: 120, align: 'right', render: optionalMoney },
                  { title: 'LSP Ref', dataIndex: 'snapshot_label', width: 160, render: (value) => value || '-' },
                  { title: 'Remark', dataIndex: 'remark', ellipsis: true, render: (value) => value || '-' },
                ]}
              />

              {Boolean(quote.internal_log_item_count) && (
                <div>
                  <Typography.Title level={5}>LSP internal Agent comparison</Typography.Title>
                  <Table<LspQuoteTaskLogItem>
                    rowKey="id"
                    size="small"
                    loading={!includedLogItems.length && logsQuery.isFetching}
                    pagination={false}
                    dataSource={logRows}
                    scroll={{ x: 1060 }}
                    columns={[
                      { title: 'Action', dataIndex: 'log_action', width: 80, render: (value) => value || '-' },
                      { title: 'Scope', dataIndex: 'item_scope', width: 90, render: (value) => value || '-' },
                      { title: 'Agent', width: 120, render: (_, item) => item.agent_name || item.agent_code || item.carrier_agent_code || 'Unknown' },
                      { title: 'Carrier', dataIndex: 'carrier_code', width: 130, render: (value) => value || '-' },
                      { title: 'Channel', dataIndex: 'channel_code', width: 130, render: (value) => value || '-' },
                      { title: 'Service', dataIndex: 'service_level', width: 120, render: (value) => value || '-' },
                      { title: 'Can Ship', dataIndex: 'can_shipping', width: 90, render: (value) => (value ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>) },
                      { title: 'Cost', dataIndex: 'shipping_cost', width: 100, align: 'right', render: optionalMoney },
                      { title: 'Surcharge', dataIndex: 'surcharge', width: 100, align: 'right', render: optionalMoney },
                      { title: 'Reason', dataIndex: 'failed_reason', ellipsis: true, render: (value) => value || '-' },
                    ]}
                  />
                </div>
              )}
            </Space>
          ),
        },
      ]}
    />
  )
}

export function ManualQuote() {
  const [form] = Form.useForm<ManualQuoteFormValues>()
  const [messageApi, contextHolder] = message.useMessage()
  const [selected, setSelected] = useState<QuoteCandidate | null>(null)
  const [skuPickerOpen, setSkuPickerOpen] = useState(false)
  const [skuPickerMode, setSkuPickerMode] = useState<SkuPickerMode>('multiple')
  const [skuSearchText, setSkuSearchText] = useState('')
  const [skuSearch, setSkuSearch] = useState('')
  const [singleSkuSearch, setSingleSkuSearch] = useState('')
  const [destinationSearch, setDestinationSearch] = useState('SOUTH MELBOURNE')
  const [orderSearchText, setOrderSearchText] = useState('')
  const [orderSearch, setOrderSearch] = useState('')
  const [selectedOrder, setSelectedOrder] = useState<OrderLookupResult | null>(null)
  const [selectedSkuMap, setSelectedSkuMap] = useState<Record<string, SkuLookupSku>>({})
  const [addingSkus, setAddingSkus] = useState(false)
  const [skuLookupByCode, setSkuLookupByCode] = useState<Record<string, SkuLookupResponse>>({})
  const quoteInputMode = (Form.useWatch('quote_input_mode', form) || 'SKU_LOOKUP') as QuoteInputMode
  const selectedDestination = Form.useWatch('destination', form)
  const { data: platforms = [] } = useQuery({ queryKey: ['platforms'], queryFn: () => listResource<Platform>('/platforms/') })
  const { data: warehouses = [] } = useQuery({ queryKey: ['warehouses'], queryFn: () => listResource<Warehouse>('/warehouses/') })
  const { data: pickerSkus = [], isFetching: pickerLoading } = useQuery({
    queryKey: ['manual-quote-skus', skuSearch],
    queryFn: async () => {
      const params = new URLSearchParams({ active: 'true', page_size: '50' })
      if (skuSearch.trim()) params.set('search', skuSearch.trim())
      const { data } = await api.get<Paginated<SkuLookupSku> | SkuLookupSku[]>(`/skus/?${params.toString()}`)
      return unpackList(data)
    },
    enabled: skuPickerOpen,
  })
  const { data: singleSkuOptions = [] } = useQuery({
    queryKey: ['manual-quote-single-skus', singleSkuSearch],
    queryFn: async () => {
      const params = new URLSearchParams({ active: 'true', page_size: '50' })
      if (singleSkuSearch.trim()) params.set('search', singleSkuSearch.trim())
      const { data } = await api.get<Paginated<SkuLookupSku> | SkuLookupSku[]>(`/skus/?${params.toString()}`)
      return unpackList(data)
    },
    enabled: singleSkuSearch.trim().length >= 2,
  })
  const { data: destinationOptions = [], isFetching: destinationLoading } = useQuery({
    queryKey: ['manual-quote-destinations', destinationSearch],
    queryFn: async () => {
      const params = new URLSearchParams()
      if (destinationSearch.trim()) params.set('search', destinationSearch.trim())
      const { data } = await api.get<DestinationOption[]>(`/rate-zones/destinations/?${params.toString()}`)
      return data
    },
    enabled: destinationSearch.trim().length >= 2,
  })
  const { data: orderOptions = [], isFetching: orderLoading } = useQuery({
    queryKey: ['manual-quote-order-lookup', orderSearch],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '20' })
      if (orderSearch.trim()) params.set('search', orderSearch.trim())
      const { data } = await api.get<OrderLookupResult[]>(`/historical-orders/order-lookup/?${params.toString()}`)
      return data
    },
    enabled: orderSearch.trim().length >= 2,
  })
  const safeDestinationOptions = useMemo(() => (Array.isArray(destinationOptions) ? destinationOptions : []), [destinationOptions])
  const safeOrderOptions = useMemo(() => (Array.isArray(orderOptions) ? orderOptions : []), [orderOptions])
  const quoteMutation = useMutation({
    mutationFn: async (values: ManualQuoteFormValues) => (await api.post<QuoteRun>('/quotes/manual', cleanQuotePayload(values))).data,
  })
  const candidates = useMemo(() => quoteMutation.data?.candidates || [], [quoteMutation.data])
  const sortedCandidates = useMemo(
    () =>
      [...candidates].sort((left, right) => {
        const leftAvailable = left.availability === 'AVAILABLE'
        const rightAvailable = right.availability === 'AVAILABLE'
        if (leftAvailable !== rightAvailable) return leftAvailable ? -1 : 1
        const leftTotal = Number(left.total_inc_gst || 0)
        const rightTotal = Number(right.total_inc_gst || 0)
        if (leftTotal !== rightTotal) return leftTotal - rightTotal
        return (left.rank || 0) - (right.rank || 0)
      }),
    [candidates],
  )
  const bestAvailableCandidateId = useMemo(() => {
    const best = sortedCandidates.find((candidate) => candidate.availability === 'AVAILABLE')
    return best?.id
  }, [sortedCandidates])
  const skuAutocompleteOptions = useMemo(
    () =>
      singleSkuOptions.map((sku) => {
        const category = skuOptionCategory(sku)
        return {
          value: sku.sku,
          label: (
            <span className="sku-autocomplete-option" title={`${category} || ${sku.sku}`}>
              <span className="sku-autocomplete-category">{category}</span>
              <span className="sku-autocomplete-separator">||</span>
              <span className="sku-autocomplete-code">{sku.sku}</span>
            </span>
          ),
        }
      }),
    [singleSkuOptions],
  )
  const orderAutocompleteOptions = useMemo(
    () =>
      safeOrderOptions.map((order) => {
        const erpOrderNo = orderErpOrderDisplay(order)
        const platformOrderNo = orderPlatformOrderDisplay(order)
        const tracking = orderTrackingDisplay(order)
        return {
          value: String(order.id),
          order,
          label: (
            <span className="order-lookup-option" title={`${erpOrderNo} | ${platformOrderNo} | ${tracking}`}>
              <span className="order-lookup-option-main">{erpOrderNo}</span>
              <span className="order-lookup-option-meta">Platform Order No: {platformOrderNo}</span>
              <span className="order-lookup-option-meta">Tracking: {tracking}</span>
              <span className="order-lookup-option-meta">
                {orderPlatformDisplay(order)} | {orderWarehouseDisplay(order)} | {order.destination_label || '-'} | {order.quote_items.length} SKU
              </span>
            </span>
          ),
        }
      }),
    [safeOrderOptions],
  )

  useEffect(() => {
    if (!platforms.length) return
    const current = form.getFieldValue('platform_code')
    if (current === 'ALL') return
    if (platforms.some((platform) => platform.code === current)) return
    const preferred = platforms.find((platform) => platform.code === 'PI2022080502320043121506')
    form.setFieldValue('platform_code', (preferred || platforms.find((platform) => platform.active) || platforms[0]).code)
  }, [form, platforms])

  useEffect(() => {
    if (!warehouses.length) return
    const current = form.getFieldValue('warehouse_code')
    if (current === 'ALL') return
    if (warehouses.some((warehouse) => warehouse.code === current)) return
    const preferred = warehouses.find((warehouse) => warehouse.code === 'BG01')
    form.setFieldValue('warehouse_code', (preferred || warehouses.find((warehouse) => warehouse.active) || warehouses[0]).code)
  }, [form, warehouses])

  const addSkuCodes = async (skuCodes: string[], closePicker = true) => {
    const uniqueCodes = Array.from(new Set(skuCodes.map((code) => code.trim()).filter(Boolean)))
    if (!uniqueCodes.length) {
      messageApi.warning('Select at least one SKU')
      return
    }
    setAddingSkus(true)
    try {
      const lookups = await Promise.all(
        uniqueCodes.map(async (sku) => (await api.get<SkuLookupResponse>(`/skus/lookup/?sku=${encodeURIComponent(sku)}`)).data),
      )
      setSkuLookupByCode((current) => {
        const next = { ...current }
        for (const lookup of lookups) next[lookup.sku.sku] = lookup
        return next
      })
      const currentItems = (form.getFieldValue('items') || []) as ManualQuoteItem[]
      const existingCodes = new Set(currentItems.map((item) => item.sku).filter(Boolean))
      const additions = lookups.map(itemFromSkuLookup).filter((item) => !existingCodes.has(item.sku))
      form.setFieldsValue({ items: [...currentItems, ...additions] })
      const skipped = lookups.length - additions.length
      messageApi.success(`Added ${additions.length} SKU line${additions.length === 1 ? '' : 's'}${skipped ? `, skipped ${skipped} duplicate` : ''}`)
      setSelectedSkuMap({})
      setSingleSkuSearch('')
      if (closePicker) setSkuPickerOpen(false)
    } catch {
      messageApi.error('SKU selection could not be added')
    } finally {
      setAddingSkus(false)
    }
  }

  const addSelectedSkus = async () => {
    await addSkuCodes(Object.keys(selectedSkuMap))
  }

  const openSkuPicker = (mode: SkuPickerMode) => {
    setSkuPickerMode(mode)
    setSelectedSkuMap({})
    setSkuPickerOpen(true)
  }

  const orderLineToManualItem = (line: OrderLookupLine): ManualQuoteItem => ({
    sku: line.sku,
    sku_input: line.sku,
    qty: line.qty || 1,
    unit_weight_kg: line.unit_weight_kg || 0,
    length_cm: line.length_cm || 0,
    width_cm: line.width_cm || 0,
    height_cm: line.height_cm || 0,
    sku_type: line.sku_type || (line.combo_component_count ? 'COMBO' : 'SKU'),
    combo_component_count: line.combo_component_count || 0,
    sku_description: line.sku_description || line.description || '',
    source: line.source,
    tracking_numbers: line.tracking_numbers || (line.tracking_no ? [line.tracking_no] : []),
    sku_master_found: line.sku_master_found,
    category: line.category,
    source_rows: line.source_rows || 0,
  })

  const applyOrderLookup = (order: OrderLookupResult) => {
    const quoteItems = (order.quote_items || []).map(orderLineToManualItem)
    if (!quoteItems.length) {
      messageApi.warning('This order has no sales or shipment SKU lines to quote')
      return
    }
    const nextValues: ManualQuoteFormValues = {
      platform_code: order.platform_code || 'ALL',
      warehouse_code: order.warehouse_code || 'ALL',
      destination_search: order.destination_label,
      destination: { ...order.destination, country: order.destination.country || 'AU' },
      quote_mode: 'CURRENT_ACTIVE',
      quote_input_mode: 'ORDER_LOOKUP',
      items: quoteItems,
      options: {
        order_lookup_id: order.id,
        order_no: order.order_no,
        quote_date: order.order_date || undefined,
        quote_item_source: order.quote_item_source,
        platform_source: order.platform_source,
        platform_raw_code: order.platform_raw_code,
        warehouse_source: order.warehouse_source,
        warehouse_raw_code: order.warehouse_raw_code,
        tracking_numbers: order.tracking_numbers || [],
      },
    }
    setSelectedOrder(order)
    setOrderSearchText(orderErpOrderDisplay(order))
    setDestinationSearch(order.destination_label || order.destination.suburb || '')
    form.setFieldsValue(nextValues)
    quoteMutation.mutate(nextValues)
  }

  const clearSkuLookupLine = (index: number, skuInput = '') => {
    const currentItems = (form.getFieldValue('items') || []) as ManualQuoteItem[]
    const currentItem = currentItems[index] || {}
    form.setFieldValue(['items', index], {
      ...currentItem,
      sku: '',
      sku_input: skuInput,
      sku_type: undefined,
      combo_component_count: 0,
      sku_description: '',
      unit_weight_kg: '',
      length_cm: '',
      width_cm: '',
      height_cm: '',
    })
  }

  const updateSkuLookupInput = (index: number, value: string) => {
    setSingleSkuSearch(value)
    const currentSku = String(form.getFieldValue(['items', index, 'sku']) || '')
    if (currentSku && value !== currentSku) {
      clearSkuLookupLine(index, value)
    }
  }

  const selectSkuForLookupLine = async (index: number, skuCode: string) => {
    const currentItems = (form.getFieldValue('items') || []) as ManualQuoteItem[]
    const currentItem = currentItems[index] || {}
    setAddingSkus(true)
    try {
      const lookup = (await api.get<SkuLookupResponse>(`/skus/lookup/?sku=${encodeURIComponent(skuCode)}`)).data
      setSkuLookupByCode((current) => ({ ...current, [lookup.sku.sku]: lookup }))
      const selectedItem = itemFromSkuLookup(lookup)
      form.setFieldValue(['items', index], {
        ...selectedItem,
        sku_input: lookup.sku.sku,
        qty: currentItem.qty || selectedItem.qty || 1,
      })
      messageApi.success(`Selected ${lookup.sku.sku}`)
    } catch {
      messageApi.error('SKU could not be selected')
    } finally {
      setAddingSkus(false)
    }
  }

  const selectSkuForManualLine = async (index: number, skuCode: string) => {
    const currentItems = (form.getFieldValue('items') || []) as ManualQuoteItem[]
    const currentItem = currentItems[index] || {}
    setAddingSkus(true)
    try {
      const lookup = (await api.get<SkuLookupResponse>(`/skus/lookup/?sku=${encodeURIComponent(skuCode)}`)).data
      const selectedItem = itemFromSkuLookup(lookup)
      form.setFieldValue(['items', index], {
        ...currentItem,
        sku: lookup.sku.sku,
        qty: currentItem.qty || selectedItem.qty || 1,
        unit_weight_kg: selectedItem.unit_weight_kg,
        length_cm: selectedItem.length_cm,
        width_cm: selectedItem.width_cm,
        height_cm: selectedItem.height_cm,
      })
      messageApi.success(`Loaded dimensions for ${lookup.sku.sku}`)
    } catch {
      messageApi.error('SKU dimensions could not be loaded')
    } finally {
      setAddingSkus(false)
    }
  }

  const updatePickerSelection = (sku: SkuLookupSku, selected: boolean) => {
    setSelectedSkuMap((current) => {
      const next = skuPickerMode === 'single' ? {} : { ...current }
      if (selected) {
        next[sku.sku] = sku
      } else {
        delete next[sku.sku]
      }
      return next
    })
  }

  const renderComponentPopover = (field: SkuField) => (
    <Form.Item noStyle shouldUpdate>
      {({ getFieldValue }) => {
        const sku = String(getFieldValue(['items', field.name, 'sku']) || '').trim()
        const count = Number(getFieldValue(['items', field.name, 'combo_component_count']) || 0)
        const details = skuLookupByCode[sku]
        if (!count) {
          return <Typography.Text type="secondary">-</Typography.Text>
        }
        if (!details?.components?.length) {
          return (
            <Typography.Text type="secondary">
              {count} component{count === 1 ? '' : 's'}
            </Typography.Text>
          )
        }
        return (
          <Popover
            title="Combo components"
            content={
              <Space direction="vertical" size={4}>
                {(details?.components || []).map((component) => (
                  <Space direction="vertical" size={0} key={`${component.combo_sku}-${component.component_sku}`}>
                    <Typography.Text>
                      {component.component_sku} x {Number(component.component_qty).toLocaleString()}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      {Number(component.component_sku_snapshot?.unit_weight_kg || 0).toLocaleString()} kg,{' '}
                      {Number(component.component_sku_snapshot?.length_cm || 0).toLocaleString()}x
                      {Number(component.component_sku_snapshot?.width_cm || 0).toLocaleString()}x
                      {Number(component.component_sku_snapshot?.height_cm || 0).toLocaleString()} cm
                    </Typography.Text>
                  </Space>
                ))}
              </Space>
            }
          >
            <Button type="link" size="small">
              {count} component{count === 1 ? '' : 's'}
            </Button>
          </Popover>
        )
      }}
    </Form.Item>
  )

  const skuModeColumns = (remove: (index: number | number[]) => void) => [
    {
      title: 'SKU / Combo SKU',
      dataIndex: 'sku',
      width: 280,
      render: (_: unknown, field: SkuField) => (
        <Space direction="vertical" size={6} className="sku-link-cell">
          <Form.Item name={[field.name, 'sku_type']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'combo_component_count']} hidden>
            <InputNumber />
          </Form.Item>
          <Form.Item name={[field.name, 'sku_description']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'sku']} hidden rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'sku_input']} className="table-form-item sku-link-input">
            <AutoComplete
              allowClear
              disabled={addingSkus}
              filterOption={false}
              onChange={(value) => updateSkuLookupInput(field.name, value)}
              onClear={() => clearSkuLookupLine(field.name)}
              onSearch={setSingleSkuSearch}
              onSelect={(value) => void selectSkuForLookupLine(field.name, String(value))}
              options={skuAutocompleteOptions}
              popupMatchSelectWidth={skuAutocompletePopupWidth}
              placeholder="Type SKU, combo SKU, or category"
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => {
              const linkedSku = getFieldValue(['items', field.name, 'sku'])
              const description = getFieldValue(['items', field.name, 'sku_description'])
              return (
                <Space direction="vertical" size={0} className="sku-line-identity">
                  <Typography.Text strong>{linkedSku || 'No SKU selected'}</Typography.Text>
                  <Typography.Text type="secondary" className="sku-line-description">
                    {description || 'Choose a SKU or combo SKU from the dropdown'}
                  </Typography.Text>
                </Space>
              )
            }}
          </Form.Item>
        </Space>
      ),
    },
    {
      title: 'Qty',
      width: 92,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'qty']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="Qty" />
        </Form.Item>
      ),
    },
    {
      title: 'Type',
      width: 104,
      render: (_: unknown, field: SkuField) => (
        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) => {
            const type = getFieldValue(['items', field.name, 'sku_type'])
            if (type === 'COMBO') {
              return <Tag color="purple">Combo</Tag>
            }
            if (type === 'SKU') {
              return <Tag color="blue">SKU</Tag>
            }
            return <Typography.Text type="secondary">Lookup</Typography.Text>
          }}
        </Form.Item>
      ),
    },
    {
      title: 'Components',
      width: 132,
      render: (_: unknown, field: SkuField) => renderComponentPopover(field),
    },
    {
      title: 'Kg',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'unit_weight_kg']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="Kg" />
        </Form.Item>
      ),
    },
    {
      title: 'L cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'length_cm']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="L" />
        </Form.Item>
      ),
    },
    {
      title: 'W cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'width_cm']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="W" />
        </Form.Item>
      ),
    },
    {
      title: 'H cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'height_cm']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="H" />
        </Form.Item>
      ),
    },
    {
      title: '',
      width: 54,
      align: 'center' as const,
      render: (_: unknown, field: SkuField) => (
        <Tooltip title="Remove line">
          <Button danger size="small" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
        </Tooltip>
      ),
    },
  ]

  const manualModeColumns = (remove: (index: number | number[]) => void) => [
    {
      title: 'SKU',
      dataIndex: 'sku',
      width: 190,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'sku']} className="table-form-item">
          <AutoComplete
            allowClear
            className="full-width"
            disabled={addingSkus}
            filterOption={false}
            onSearch={setSingleSkuSearch}
            onSelect={(value) => void selectSkuForManualLine(field.name, String(value))}
            options={skuAutocompleteOptions}
            popupMatchSelectWidth={skuAutocompletePopupWidth}
            placeholder="Optional SKU"
          />
        </Form.Item>
      ),
    },
    {
      title: 'Qty',
      width: 92,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'qty']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="Qty" />
        </Form.Item>
      ),
    },
    {
      title: 'Kg',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'unit_weight_kg']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="Kg" />
        </Form.Item>
      ),
    },
    {
      title: 'L cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'length_cm']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="L" />
        </Form.Item>
      ),
    },
    {
      title: 'W cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'width_cm']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="W" />
        </Form.Item>
      ),
    },
    {
      title: 'H cm',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'height_cm']} rules={[{ required: true }]} className="table-form-item">
          <InputNumber min={0.001} placeholder="H" />
        </Form.Item>
      ),
    },
    {
      title: '',
      width: 54,
      align: 'center' as const,
      render: (_: unknown, field: SkuField) => (
        <Tooltip title="Remove line">
          <Button danger size="small" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
        </Tooltip>
      ),
    },
  ]

  const orderModeColumns = () => [
    {
      title: 'SKU / Combo SKU',
      dataIndex: 'sku',
      width: 300,
      render: (_: unknown, field: SkuField) => (
        <Space direction="vertical" size={4} className="sku-link-cell">
          <Form.Item name={[field.name, 'sku']} hidden rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'sku_input']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'sku_type']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'combo_component_count']} hidden>
            <InputNumber />
          </Form.Item>
          <Form.Item name={[field.name, 'sku_description']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'category']} hidden>
            <Input />
          </Form.Item>
          <Form.Item name={[field.name, 'tracking_numbers']} hidden>
            <Select mode="tags" />
          </Form.Item>
          <Form.Item name={[field.name, 'source_rows']} hidden>
            <InputNumber />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => {
              const sku = getFieldValue(['items', field.name, 'sku'])
              const description = getFieldValue(['items', field.name, 'sku_description'])
              const category = getFieldValue(['items', field.name, 'category'])
              return (
                <Space direction="vertical" size={0} className="sku-line-identity">
                  <Typography.Text strong>{sku || '-'}</Typography.Text>
                  <Typography.Text type="secondary" className="sku-line-description">
                    {[category, description].filter(Boolean).join(' | ') || 'Order SKU snapshot'}
                  </Typography.Text>
                </Space>
              )
            }}
          </Form.Item>
        </Space>
      ),
    },
    {
      title: 'Qty',
      width: 92,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'qty']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="Qty" />
        </Form.Item>
      ),
    },
    {
      title: 'Type',
      width: 104,
      render: (_: unknown, field: SkuField) => (
        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) => {
            const type = getFieldValue(['items', field.name, 'sku_type'])
            if (type === 'COMBO') return <Tag color="purple">Combo</Tag>
            return <Tag color="blue">SKU</Tag>
          }}
        </Form.Item>
      ),
    },
    {
      title: 'Source',
      width: 106,
      render: (_: unknown, field: SkuField) => (
        <>
          <Form.Item name={[field.name, 'source']} hidden>
            <Input />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => <Tag>{getFieldValue(['items', field.name, 'source']) || 'order'}</Tag>}
          </Form.Item>
        </>
      ),
    },
    {
      title: 'Tracking',
      width: 170,
      render: (_: unknown, field: SkuField) => (
        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) => {
            const values = (getFieldValue(['items', field.name, 'tracking_numbers']) || []) as string[]
            return <Typography.Text className="order-tracking-cell">{values.join(', ') || '-'}</Typography.Text>
          }}
        </Form.Item>
      ),
    },
    {
      title: 'Components',
      width: 118,
      render: (_: unknown, field: SkuField) => renderComponentPopover(field),
    },
    {
      title: 'Kg',
      width: 96,
      render: (_: unknown, field: SkuField) => (
        <Form.Item name={[field.name, 'unit_weight_kg']} className="table-form-item">
          <InputNumber disabled min={0} placeholder="Kg" />
        </Form.Item>
      ),
    },
    {
      title: 'L/W/H cm',
      width: 190,
      render: (_: unknown, field: SkuField) => (
        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) => {
            const length = Number(getFieldValue(['items', field.name, 'length_cm']) || 0).toFixed(2)
            const width = Number(getFieldValue(['items', field.name, 'width_cm']) || 0).toFixed(2)
            const height = Number(getFieldValue(['items', field.name, 'height_cm']) || 0).toFixed(2)
            return `${length} / ${width} / ${height}`
          }}
        </Form.Item>
      ),
    },
  ]

  return (
    <section className="quote-workspace">
      {contextHolder}
      <div className="quote-input">
        <Typography.Title level={2}>Manual Quote</Typography.Title>
        <Typography.Text type="secondary">Quote every enabled carrier/channel for one destination and shipment profile.</Typography.Text>
        <Card className="section-block">
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              platform_code: 'ALL',
              warehouse_code: 'ALL',
              destination_search: 'SOUTH MELBOURNE, VIC 3205',
              destination: { state: 'VIC', suburb: 'SOUTH MELBOURNE', postcode: '3205', country: 'AU' },
              quote_mode: 'CURRENT_ACTIVE',
              quote_input_mode: 'MANUAL_DIMENSIONS',
              items: [
                {
                  sku: '',
                  qty: 1,
                  unit_weight_kg: 12,
                  length_cm: 80,
                  width_cm: 60,
                  height_cm: 45,
                },
              ],
              options: {},
            }}
            onFinish={(values) => quoteMutation.mutate(values)}
          >
            <Form.Item name="platform_code" label="Platform" rules={[{ required: true }]}>
              <Select
                showSearch
                optionFilterProp="label"
                options={[
                  { value: 'ALL', label: 'All platforms' },
                  ...platforms.map((item) => ({ value: item.code, label: `${item.code} - ${item.name}` })),
                ]}
              />
            </Form.Item>
            <Form.Item name="warehouse_code" label="Warehouse" rules={[{ required: true }]}>
              <Select
                showSearch
                optionFilterProp="label"
                options={[{ value: 'ALL', label: 'All warehouses' }, ...warehouses.map((item) => ({ value: item.code, label: warehouseLabel(item) }))]}
              />
            </Form.Item>
            <Form.Item name="destination_search" label="Suburb" rules={[{ required: true }]}>
              <AutoComplete
                allowClear
                options={safeDestinationOptions.map((item) => ({
                  value: item.label,
                  label: `${item.suburb}, ${item.state} ${item.postcode}`,
                  destination: item,
                }))}
                onSearch={(value) => {
                  setDestinationSearch(value)
                  form.setFieldsValue({ destination: { state: '', suburb: value.toUpperCase(), postcode: '', country: 'AU' } })
                }}
                onSelect={(_, option) => {
                  const destination = (option as { destination?: DestinationOption }).destination
                  if (!destination) return
                  form.setFieldsValue({
                    destination_search: destination.label,
                    destination: {
                      state: destination.state,
                      suburb: destination.suburb,
                      postcode: destination.postcode,
                      country: 'AU',
                    },
                  })
                }}
                placeholder="Start typing suburb, then choose postcode/state"
                notFoundContent={destinationLoading ? 'Searching...' : undefined}
              />
            </Form.Item>
            <Form.Item name={['destination', 'state']} hidden rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name={['destination', 'suburb']} hidden rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name={['destination', 'postcode']} hidden rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <div className="destination-summary">
              <Tag color="blue">{selectedDestination?.suburb || 'SUBURB'}</Tag>
              <Tag>{selectedDestination?.state || 'STATE'}</Tag>
              <Tag>{selectedDestination?.postcode || 'POSTCODE'}</Tag>
            </div>
            <Form.Item name="quote_input_mode" label="Line entry mode">
              <Segmented
                block
                options={[
                  { label: 'SKU / Combo SKU', value: 'SKU_LOOKUP' },
                  { label: 'Manual dimensions', value: 'MANUAL_DIMENSIONS' },
                  { label: 'ERP / Platform Order', value: 'ORDER_LOOKUP' },
                ]}
              />
            </Form.Item>
            {quoteInputMode === 'ORDER_LOOKUP' && (
              <div className="order-lookup-panel">
                <Form.Item label="ERP Order No / Platform Order No">
                  <AutoComplete
                    allowClear
                    className="full-width"
                    value={orderSearchText}
                    filterOption={false}
                    options={orderAutocompleteOptions}
                    onChange={(value) => {
                      setOrderSearchText(value)
                      if (!value) {
                        setOrderSearch('')
                        setSelectedOrder(null)
                      }
                    }}
                    onSearch={(value) => {
                      setOrderSearchText(value)
                      setOrderSearch(value)
                    }}
                    onSelect={(_value, option) => {
                      const order = (option as { order?: OrderLookupResult }).order
                      if (order) applyOrderLookup(order)
                    }}
                    placeholder="Type ERP order no, platform order no, 3rd party no, consignment, or tracking"
                    notFoundContent={orderLoading ? 'Searching...' : undefined}
                  />
                </Form.Item>
                {selectedOrder && (
                  <Space direction="vertical" size={12} className="full-width">
                    <Descriptions bordered size="small" column={3} className="order-lookup-summary">
                      <Descriptions.Item label="ERP Order No">{orderErpOrderDisplay(selectedOrder)}</Descriptions.Item>
                      <Descriptions.Item label="Platform Order No">
                        {orderPlatformOrderValues(selectedOrder).length ? (
                          <Space size={4} wrap>
                            {orderPlatformOrderValues(selectedOrder).map((value) => (
                              <Tag key={value}>{value}</Tag>
                            ))}
                          </Space>
                        ) : (
                          '-'
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="Tracking">
                        {orderTrackingValues(selectedOrder).length ? (
                          <Space size={4} wrap>
                            {orderTrackingValues(selectedOrder).map((value) => (
                              <Tag key={value}>{value}</Tag>
                            ))}
                          </Space>
                        ) : (
                          '-'
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="Platform">
                        <Space size={4} wrap>
                          <span>{orderPlatformDisplay(selectedOrder)}</span>
                          {selectedOrder.platform_code === 'ALL' && selectedOrder.platform_raw_code && (
                            <Typography.Text type="secondary">({selectedOrder.platform_raw_code})</Typography.Text>
                          )}
                          {sourceLabel(selectedOrder.platform_source) && <Tag>{sourceLabel(selectedOrder.platform_source)}</Tag>}
                        </Space>
                      </Descriptions.Item>
                      <Descriptions.Item label="Warehouse">
                        <Space size={4} wrap>
                          <span>{orderWarehouseDisplay(selectedOrder)}</span>
                          {selectedOrder.warehouse_code === 'ALL' && selectedOrder.warehouse_raw_code && (
                            <Typography.Text type="secondary">({selectedOrder.warehouse_raw_code})</Typography.Text>
                          )}
                          {sourceLabel(selectedOrder.warehouse_source) && <Tag>{sourceLabel(selectedOrder.warehouse_source)}</Tag>}
                        </Space>
                      </Descriptions.Item>
                      <Descriptions.Item label="Destination">{selectedOrder.destination_label}</Descriptions.Item>
                      <Descriptions.Item label="Shipping option">{selectedOrder.shipping_option || '-'}</Descriptions.Item>
                      <Descriptions.Item label="ERP Carrier">
                        {selectedOrder.actual_carrier ? <Tag>{selectedOrder.actual_carrier}</Tag> : '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="ERP Est inc GST">
                        {selectedOrder.postage_shipping_estimated_amount
                          ? erpEstimateMoney(selectedOrder.postage_shipping_estimated_amount)
                          : selectedOrder.source_estimated_freight
                            ? erpEstimateMoney(selectedOrder.source_estimated_freight)
                            : '-'}
                      </Descriptions.Item>
                    </Descriptions>
                    <OrderLspQuoteCollapse quote={selectedOrder.lsp_quote} />
                    <Tabs
                      size="small"
                      className="order-lookup-detail-tabs"
                      items={[
                        {
                          key: 'quote',
                          label: `Quote SKU (${selectedOrder.quote_items.length})`,
                          children: (
                            <Table<OrderLookupLine>
                              rowKey={(row, index) => `${row.sku}-${index}`}
                              size="small"
                              pagination={false}
                              dataSource={selectedOrder.quote_items}
                              columns={[
                                { title: 'SKU', dataIndex: 'sku', width: 180, ellipsis: true },
                                { title: 'Source', dataIndex: 'source', width: 90, render: (value) => <Tag>{value || selectedOrder.quote_item_source}</Tag> },
                                { title: 'Qty', dataIndex: 'qty', width: 90, align: 'right' },
                                {
                                  title: 'Type',
                                  width: 100,
                                  render: (_, record) => <Tag color={record.sku_type === 'COMBO' ? 'purple' : 'blue'}>{record.sku_type || 'SKU'}</Tag>,
                                },
                                { title: 'Category', dataIndex: 'category', width: 150, ellipsis: true, render: (value) => value || '-' },
                                { title: 'Tracking', dataIndex: 'tracking_numbers', ellipsis: true, render: (values) => values?.join(', ') || '-' },
                                { title: 'Kg', dataIndex: 'unit_weight_kg', width: 90, align: 'right' },
                                {
                                  title: 'L / W / H cm',
                                  width: 160,
                                  render: (_, record) => `${record.length_cm || 0} / ${record.width_cm || 0} / ${record.height_cm || 0}`,
                                },
                              ]}
                              scroll={{ x: 1080 }}
                            />
                          ),
                        },
                        {
                          key: 'sales',
                          label: `Sales SKU (${selectedOrder.sales_items.length})`,
                          children: (
                            <Table<OrderLookupLine>
                              rowKey={(row, index) => `${row.sku}-${index}`}
                              size="small"
                              pagination={false}
                              dataSource={selectedOrder.sales_items}
                              columns={[
                                { title: 'SKU', dataIndex: 'sku', width: 180, ellipsis: true },
                                { title: 'Description', dataIndex: 'description', ellipsis: true },
                                { title: 'Qty', dataIndex: 'qty', width: 90, align: 'right' },
                                { title: 'Kg', dataIndex: 'unit_weight_kg', width: 90, align: 'right' },
                                {
                                  title: 'L / W / H cm',
                                  width: 160,
                                  render: (_, record) => `${record.length_cm || 0} / ${record.width_cm || 0} / ${record.height_cm || 0}`,
                                },
                              ]}
                              scroll={{ x: 760 }}
                            />
                          ),
                        },
                        {
                          key: 'shipment',
                          label: `Shipment SKU (${selectedOrder.shipment_items.length})`,
                          children: (
                            <Table<OrderLookupLine>
                              rowKey={(row, index) => `${row.tracking_no || row.sku}-${index}`}
                              size="small"
                              pagination={false}
                              dataSource={selectedOrder.shipment_items}
                              columns={[
                                { title: 'Tracking', dataIndex: 'tracking_no', width: 150, ellipsis: true },
                                { title: 'SKU', dataIndex: 'sku', width: 180, ellipsis: true },
                                { title: 'Qty', dataIndex: 'qty', width: 90, align: 'right' },
                                { title: 'Carrier', dataIndex: 'carrier_name', width: 150, ellipsis: true },
                                { title: 'Channel', dataIndex: 'carrier_channel', width: 150, ellipsis: true },
                                { title: 'Warehouse', dataIndex: 'warehouse_code', width: 120, render: (value, record) => value || record.warehouse_owner_code || '-' },
                              ]}
                              scroll={{ x: 840 }}
                            />
                          ),
                        },
                      ]}
                    />
                  </Space>
                )}
              </div>
            )}
            <Form.List name="items">
              {(fields, { add, remove }) => (
                <Table<SkuField>
                  className="sku-lines-table"
                  rowKey="key"
                  size="small"
                  dataSource={fields}
                  pagination={false}
                  scroll={{ x: quoteInputMode === 'ORDER_LOOKUP' ? 1180 : quoteInputMode === 'SKU_LOOKUP' ? 1080 : 720 }}
                  title={() => (
                    <div className="sku-lines-title">
                      <div className="table-title-row">
                        <Typography.Text strong>SKU Lines</Typography.Text>
                        {quoteInputMode === 'MANUAL_DIMENSIONS' && (
                          <Button size="small" icon={<PlusOutlined />} onClick={() => add({ qty: 1 })}>
                            Add line
                          </Button>
                        )}
                      </div>
                      {quoteInputMode === 'SKU_LOOKUP' && (
                        <div className="sku-association-bar">
                          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => openSkuPicker('multiple')}>
                            Select multiple SKU
                          </Button>
                        </div>
                      )}
                      {quoteInputMode === 'ORDER_LOOKUP' && (
                        <Typography.Text type="secondary">
                          Using {selectedOrder?.quote_item_source || 'order'} SKU snapshot. Combo SKUs are expanded by the backend calculator.
                        </Typography.Text>
                      )}
                    </div>
                  )}
                  columns={
                    quoteInputMode === 'ORDER_LOOKUP'
                      ? orderModeColumns()
                      : quoteInputMode === 'SKU_LOOKUP'
                        ? skuModeColumns(remove)
                        : manualModeColumns(remove)
                  }
                />
              )}
            </Form.List>
            <Button block type="primary" htmlType="submit" icon={<SearchOutlined />} loading={quoteMutation.isPending}>
              Query All Rates
            </Button>
          </Form>
        </Card>
      </div>
      <Modal
        title={skuPickerMode === 'single' ? 'Link one SKU' : 'Select multiple SKU'}
        open={skuPickerOpen}
        width={980}
        onCancel={() => setSkuPickerOpen(false)}
        onOk={() => void addSelectedSkus()}
        okText={`${skuPickerMode === 'single' ? 'Link' : 'Add'} ${Object.keys(selectedSkuMap).length ? `${Object.keys(selectedSkuMap).length} ` : ''}SKU${
          Object.keys(selectedSkuMap).length === 1 ? '' : 's'
        }`}
        confirmLoading={addingSkus}
      >
        <Space direction="vertical" className="full-width" size="middle">
          <Input.Search
            allowClear
            placeholder="Search SKU, combo SKU, description, or category"
            value={skuSearchText}
            onChange={(event) => setSkuSearchText(event.target.value)}
            onSearch={(value) => setSkuSearch(value)}
          />
          <Table<SkuLookupSku>
            rowKey="sku"
            size="small"
            loading={pickerLoading}
            dataSource={pickerSkus}
            pagination={{ pageSize: 8, showSizeChanger: false }}
            rowSelection={{
              type: skuPickerMode === 'single' ? 'radio' : 'checkbox',
              selectedRowKeys: Object.keys(selectedSkuMap),
              preserveSelectedRowKeys: true,
              onSelect: (record, selectedRow) => updatePickerSelection(record, selectedRow),
              onSelectAll: (selectedRows, _selectedRows, changedRows) => {
                for (const row of changedRows) updatePickerSelection(row, selectedRows)
              },
            }}
            columns={[
              { title: 'SKU', dataIndex: 'sku', width: 180, ellipsis: true },
              { title: 'Description', dataIndex: 'description', ellipsis: true },
              { title: 'Category', dataIndex: 'category', width: 160, ellipsis: true, render: (value) => value || '-' },
              {
                title: 'Type',
                width: 110,
                render: (_, record) => <Tag color={record.is_combo ? 'purple' : 'blue'}>{record.is_combo ? record.combo_type_label || 'combo' : 'SKU'}</Tag>,
              },
              { title: 'Kg', dataIndex: 'unit_weight_kg', width: 90, align: 'right', render: (value) => Number(value || 0).toFixed(3) },
              {
                title: 'L / W / H cm',
                width: 160,
                render: (_, record) =>
                  `${Number(record.length_cm || 0).toFixed(2)} / ${Number(record.width_cm || 0).toFixed(2)} / ${Number(record.height_cm || 0).toFixed(2)}`,
              },
              { title: 'Components', dataIndex: 'combo_component_count', width: 110, align: 'right' },
            ]}
          />
        </Space>
      </Modal>
      <div className="quote-results">
        <div className="page-toolbar">
          <div>
            <Typography.Title level={2}>Quote Results</Typography.Title>
            <Typography.Text type="secondary">{quoteMutation.data ? `QuoteRun #${quoteMutation.data.id}` : 'Waiting for query'}</Typography.Text>
          </div>
        </div>
        <Table<QuoteCandidate>
          rowKey="id"
          dataSource={sortedCandidates}
          loading={quoteMutation.isPending}
          size="middle"
          rowClassName={(record) => (record.id === bestAvailableCandidateId ? 'quote-best-price-row' : '')}
          columns={[
            { title: '#', width: 56, render: (_value, _record, index) => index + 1 },
            { title: 'Carrier', dataIndex: 'carrier_name', render: (value, record) => value || record.carrier_code || '-' },
            { title: 'Channel', dataIndex: 'channel_code' },
            { title: 'Status', dataIndex: 'availability', render: (value) => <Tag color={value === 'AVAILABLE' ? 'green' : 'red'}>{value}</Tag> },
            {
              title: 'Total inc GST',
              dataIndex: 'total_inc_gst',
              align: 'right',
              render: (value, record) => (
                <Typography.Text strong={record.id === bestAvailableCandidateId}>{money(value)}</Typography.Text>
              ),
            },
            { title: 'Reason', dataIndex: 'not_available_reason' },
            {
              title: '',
              width: 150,
              render: (_, record) => (
                <Button size="small" icon={<EyeOutlined />} onClick={() => setSelected(record)}>
                  View Breakdown
                </Button>
              ),
            },
          ]}
          pagination={false}
        />
      </div>
      <Drawer size="large" open={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.provider_name}>
        {selected && (
          <Space direction="vertical" size="large" className="full-width">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="Carrier">{selected.carrier_name || selected.carrier_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="Channel">{selected.channel_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="Availability">{selected.availability}</Descriptions.Item>
              <Descriptions.Item label="Final price">{money(selected.total_inc_gst)}</Descriptions.Item>
              <Descriptions.Item label="Reason" span={2}>
                {selected.not_available_reason || '-'}
              </Descriptions.Item>
            </Descriptions>
            <Tabs
              items={[
                {
                  key: 'breakdown',
                  label: 'Breakdown',
                  children: (
                    <Table
                      size="small"
                      rowKey="id"
                      dataSource={nonZeroChargeLines(selected.charge_lines)}
                      pagination={false}
                      summary={() => (
                        <Table.Summary.Row>
                          <Table.Summary.Cell index={0} colSpan={2}>
                            <Typography.Text strong>Final price</Typography.Text>
                          </Table.Summary.Cell>
                          <Table.Summary.Cell index={2} align="right">
                            <Typography.Text strong>{money(selected.total_ex_gst)}</Typography.Text>
                          </Table.Summary.Cell>
                          <Table.Summary.Cell index={3} align="right">
                            <Typography.Text strong>{money(selected.gst_amount)}</Typography.Text>
                          </Table.Summary.Cell>
                          <Table.Summary.Cell index={4} align="right">
                            <Typography.Text strong>{money(selected.total_inc_gst)}</Typography.Text>
                          </Table.Summary.Cell>
                        </Table.Summary.Row>
                      )}
                      columns={[
                        { title: 'Type', dataIndex: 'line_type', width: 120 },
                        { title: 'Description', dataIndex: 'description' },
                        { title: 'Ex GST', dataIndex: 'amount_ex_gst', align: 'right', render: money },
                        { title: 'GST', dataIndex: 'gst_amount', align: 'right', render: money },
                        { title: 'Inc GST', dataIndex: 'amount_inc_gst', align: 'right', render: money },
                      ]}
                    />
                  ),
                },
                {
                  key: 'trace',
                  label: 'Trace',
                  children: (
                    <Space direction="vertical" className="full-width" size="middle">
                      {(selected.trace_logs || []).map((trace) => (
                        <Card size="small" key={trace.id} title={`${trace.event_type} - ${trace.step}`}>
                          <Typography.Paragraph>{trace.message}</Typography.Paragraph>
                          <pre className="debug-json">{JSON.stringify(trace.details_json, null, 2)}</pre>
                        </Card>
                      ))}
                      {!selected.trace_logs?.length && <pre className="debug-json">{JSON.stringify(selected.debug_breakdown, null, 2)}</pre>}
                    </Space>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </section>
  )
}
