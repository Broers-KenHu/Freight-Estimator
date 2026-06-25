export type UserProfile = {
  id: number
  email: string
  display_name: string
  role: string
  auth_provider: 'LOCAL' | 'ENTRA' | 'HYBRID'
  entra_oid?: string
  entra_upn?: string
  entra_tid?: string
  permission_overrides?: string[]
  role_permissions?: string[]
  require_password_change?: boolean
  has_local_password?: boolean
  is_active: boolean
  last_auth_source?: string
  last_login_at?: string | null
  permissions: string[]
}

export type PermissionCatalogGroup = {
  group: string
  permissions: { code: string; label: string }[]
}

export type RoleCatalogItem = {
  code: string
  label: string
  description: string
  permissions: string[]
  resolved_permissions: string[]
}

export type Platform = {
  id: number
  code: string
  name: string
  company?: string
  platform_type: string
  platform_role: string
  source_platform_type_code?: number | null
  source_platform_type_name_en?: string
  source_platform_type_name_zh?: string
  platform_group_code?: number | null
  platform_group_name_en?: string
  platform_group_name_zh?: string
  active: boolean
}

export type CarrierService = {
  id: number
  carrier: number
  carrier_code: string
  carrier_name: string
  code: string
  name: string
  service_level?: string
  active: boolean
}

export type Carrier = {
  id: number
  code: string
  name: string
  carrier_type: string
  support_api: boolean
  active: boolean
  services?: CarrierService[]
}

export type Agent = {
  id: number
  code: string
  name: string
  agent_type: 'LSP' | 'API' | 'RATE_OWNER' | 'OTHER' | string
  active: boolean
  supports_api: boolean
  maintains_rate_cards: boolean
  lsp_status_code?: number | null
  lsp_rate_type?: number | null
  lsp_consign_agent_id?: string
  channel_count?: number
  carrier_count?: number
  source_system?: string
  external_updated_at?: string | null
}

export type InvoiceSource = {
  id: number
  code: string
  name: string
  source_platform: string
  freight_account: string
  carrier: number | null
  carrier_code?: string
  carrier_name?: string
  carrier_service: number | null
  carrier_service_code?: string
  carrier_service_name?: string
  mapping_method: string
  active: boolean
  auto_created_carrier: boolean
  auto_created_service: boolean
}

export type PlatformCarrier = {
  id: number
  platform: number
  platform_code: string
  carrier: number
  carrier_code: string
  carrier_name: string
  service: number
  service_code: string
  service_name: string
  enabled: boolean
  account_code?: string
  priority: number
  quote_source: string
}

export type Warehouse = {
  id: number
  code: string
  name: string
  address?: string
  address2?: string
  suburb: string
  postcode: string
  state: string
  region?: string
  contact_name?: string
  telephone?: string
  active: boolean
}

export type RateCard = {
  id: number
  carrier: number
  carrier_code: string
  carrier_name: string
  service: number | null
  service_code?: string
  name: string
  version: string
  status: string
  effective_status: string
  active_now: boolean
  is_active: boolean
  priority: number
  effective_from?: string | null
  effective_to?: string | null
  uploaded_by_email?: string
  approved_by_email?: string
  approved_at?: string | null
  rule_count?: number
  zone_count?: number
  surcharge_count?: number
  quote_channel_count?: number
}

export type QuoteChargeLine = {
  id: number
  line_type: string
  description: string
  amount_ex_gst: string
  gst_amount: string
  amount_inc_gst: string
}

export type QuoteTraceLog = {
  id: number
  event_type: string
  step: string
  message: string
  details_json: Record<string, unknown>
  channel_code?: string
  provider_name?: string
  created_at: string
}

export type QuoteCandidate = {
  id: number
  rank: number
  provider_name: string
  carrier_code: string
  carrier_name: string
  service_code: string
  channel_code: string
  availability: 'AVAILABLE' | 'NOT_AVAILABLE'
  not_available_reason: string
  base_amount: string
  surcharge_amount: string
  fuel_amount: string
  adjustment_amount: string
  total_ex_gst: string
  gst_amount: string
  total_inc_gst: string
  charge_lines: QuoteChargeLine[]
  trace_logs: QuoteTraceLog[]
  debug_breakdown: Record<string, unknown>
}

export type QuoteRun = {
  id: number
  run_type: string
  status: string
  input_hash: string
  created_at: string
  candidate_count?: number
  candidates?: QuoteCandidate[]
  trace_logs?: QuoteTraceLog[]
}

export type QuoteChannel = {
  id: number
  code: string
  name: string
  carrier: number
  carrier_code: string
  carrier_name: string
  service: number | null
  service_code?: string
  provider_type: string
  calculator_key: string
  enabled: boolean
  priority: number
  rate_card: number | null
  agent: number | null
  agent_code?: string
  agent_name?: string
}

export type InvoiceReconciliationItem = {
  id: number
  order_no: string
  consignment_no: string
  invoice_no: string
  carrier_code: string
  carrier_name: string
  carrier_service_code?: string
  carrier_service_name?: string
  invoice_source_code?: string
  invoice_source_name?: string
  quote_provider?: string
  estimated_freight: string | null
  estimated_freight_inc_gst?: string | null
  estimated_freight_basis?: string
  system_estimated_freight?: string | null
  actual_freight: string
  variance_amount: string | null
  variance_percent: string | null
  system_variance_amount?: string | null
  system_variance_percent?: string | null
  match_status: string
  variance_type: string
  dispute_recommended: boolean
  reason: string
  system_estimate_reason?: string
  amount_detail?: {
    erp_estimate_source?: string | null
    erp_estimate_ex_gst?: string | null
    erp_estimate_inc_gst?: string | null
    erp_estimate_basis?: string | null
    system_estimate_inc_gst?: string | null
    actual_invoice_inc_gst?: string | null
    erp_variance_inc_gst?: string | null
    erp_variance_percent?: string | null
    system_variance_inc_gst?: string | null
    system_variance_percent?: string | null
  }
  invoice_match_detail?: Record<string, string | null> | null
  order_detail?: Record<string, string | number | null>
}

export type InvoiceReconciliationBatch = {
  id: number
  name: string
  carrier_code?: string
  carrier_name?: string
  carrier_service_code?: string
  carrier_service_name?: string
  invoice_source_code?: string
  invoice_source_name?: string
  status: string
  total_rows: number
  matched_rows: number
  exception_rows: number
  created_at: string
  items?: InvoiceReconciliationItem[]
}

export type FreightAuditChargeLine = {
  tracking?: string
  type: string
  description: string
  amount_ex_gst: string
  gst_amount: string
  amount_inc_gst: string
}

export type FreightAuditItem = {
  sku: string
  qty: string | number
  unit_weight_kg?: string | number
  length_cm?: string | number
  width_cm?: string | number
  height_cm?: string | number
  actual_kg?: string | number
  cubic_kg?: string | number
  cubic_factor?: string | number
  category?: string
  description?: string
  calculation_source?: string
  combo_parent_sku?: string
  combo_parent_qty?: string | number
  combo_component_qty?: string | number
}

export type FreightAuditComponent = {
  tracking: string
  quote_run_id?: number
  quote_candidate_id?: number
  availability: string
  not_available_reason?: string
  total_inc_gst?: string | null
  debug_breakdown?: Record<string, unknown>
  items?: FreightAuditItem[]
}

export type FreightAuditResult = {
  id: number
  row: number
  quote_channel: number | null
  quote_channel_code?: string
  quote_candidate: number | null
  quote_candidate_id?: number
  carrier: number | null
  carrier_service: number | null
  carrier_key: string
  carrier_name: string
  service_name: string
  provider_type: string
  availability: 'AVAILABLE' | 'NOT_AVAILABLE' | string
  not_available_reason: string
  base_amount: string | null
  surcharge_amount: string | null
  fuel_amount: string | null
  adjustment_amount: string | null
  gst_amount: string | null
  total_inc_gst: string | null
  variance_to_erp: string | null
  variance_to_invoice: string | null
  rank: number | null
  raw_payload: {
    provider_name?: string
    channel_code?: string
    rate_card?: string
    debug_breakdown?: Record<string, unknown>
    charge_lines?: FreightAuditChargeLine[]
    items?: FreightAuditItem[]
    components?: FreightAuditComponent[]
  }
}

export type FreightAuditRow = {
  id: number
  source_system: string
  source_external_id: string
  calculation_mode: 'ORDER' | 'CONSIGNMENT' | 'ITEM' | string
  invoice_reconciliation_item: number | null
  erp_shipment_snapshot: number | null
  quote_run: number | null
  order_no: string
  tracking_no: string
  platform_code: string
  platform_name: string
  warehouse_code: string
  order_date: string | null
  suburb: string
  postcode: string
  state: string
  erp_estimated_freight: string | null
  invoice_actual_freight: string | null
  item_count: number
  total_qty: string
  status: string
  error_message: string
  raw_payload: Record<string, unknown>
  results: FreightAuditResult[]
  best_results: Record<string, FreightAuditResult>
  created_at: string
  updated_at: string
}

export type FreightAuditCarrierSummary = {
  quote_channel_id: number
  quote_channel_code: string
  quote_channel_name: string
  agent_code: string
  agent_name: string
  carrier_id: number
  carrier_key: string
  carrier_code: string
  carrier_name: string
  service_id: number | null
  service_code: string
  service_name: string
  provider_type: string
  calculator_key: string
  rate_card_id: number | null
  rate_card_name: string
  rate_card_version: string
  rate_card_status: string
  result_count: number
  audit_rows: number
  available_rows: number
  invoice_rows: number
  available_invoice_rows: number
  system_estimated_total: string | null
  invoice_actual_total: string | null
  erp_estimated_total_inc_gst: string | null
  system_minus_invoice_total: string | null
  erp_minus_invoice_total: string | null
  system_variance_to_invoice_total: string | null
}
