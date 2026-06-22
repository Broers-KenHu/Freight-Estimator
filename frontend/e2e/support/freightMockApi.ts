import type { Page, Route } from '@playwright/test'

type JsonValue = Record<string, unknown> | Record<string, unknown>[] | unknown[]

export type FreightMockState = {
  lastQuotePayload?: Record<string, unknown>
  lastAuditBuildPayload?: Record<string, unknown>
  lastInvoiceExport?: { batchId: string; scope: string }
  searches: Array<{ path: string; search: string }>
}

const paginated = <T extends Record<string, unknown>>(results: T[]) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
})

const user = {
  id: 1,
  email: 'admin@couriedelivery.local',
  display_name: 'E2E Admin',
  role: 'ADMIN',
  auth_provider: 'LOCAL',
  is_active: true,
  permissions: ['*'],
}

const platforms = [
  {
    id: 1,
    code: 'PI2022080502320043121506',
    name: 'Fantastic',
    company: 'Fantastic Furniture',
    platform_type: 'MARKETPLACE',
    platform_role: 'SALES',
    active: true,
  },
]

const warehouses = [
  { id: 1, code: 'BG01', name: 'BG01', state: 'VIC', suburb: 'DERRIMUT', postcode: '3026', active: true },
  { id: 2, code: 'BGS1', name: 'BGS1', state: 'NSW', suburb: 'SMITHFIELD', postcode: '2164', active: true },
]

const carriers = [
  { id: 1, code: '758', name: 'Allied Express', carrier_type: 'HYBRID', support_api: true, active: true, active_rate_rows: 16968 },
  { id: 2, code: 'road_freight', name: 'Hunter Road Freight', carrier_type: 'TABLE', support_api: false, active: true, active_rate_rows: 16549 },
  { id: 3, code: '454', name: 'Direct Freight Express', carrier_type: 'HYBRID', support_api: true, active: true, active_rate_rows: 16257 },
  { id: 4, code: 'orange_connex', name: 'Orange Connex', carrier_type: 'TABLE', support_api: false, active: true, active_rate_rows: 2762 },
]

const services = [
  { id: 1, carrier: 1, carrier_code: '758', carrier_name: 'Allied Express', code: 'GRO_2023_MEL', name: 'Allied GRO 2023 Melbourne', service_level: 'MEL', active: true },
  { id: 2, carrier: 2, carrier_code: 'road_freight', carrier_name: 'Hunter Road Freight', code: 'HUNTER_MEL_2023', name: 'Hunter MEL 2023', service_level: 'MEL', active: true },
  { id: 3, carrier: 3, carrier_code: '454', carrier_name: 'Direct Freight Express', code: 'DFE_KILO_EX_MEL_2025', name: 'DFE KILO EX MEL 2025', service_level: 'KILO', active: true },
]

const rateCards = [
  {
    id: 1,
    carrier: 1,
    carrier_code: '758',
    carrier_name: 'Allied Express',
    service: 1,
    service_code: 'GRO_2023_MEL',
    name: 'Allied GRO 2023 Melbourne PostageCalculator SP',
    version: 'SP-ALLIED-GRO-MEL-2023',
    status: 'ACTIVE',
    effective_status: 'Active',
    active_now: true,
    is_active: true,
    priority: 80,
    effective_from: '2023-01-01',
    rule_count: 189,
    zone_count: 16968,
    surcharge_count: 31,
    quote_channel_count: 1,
  },
  {
    id: 2,
    carrier: 2,
    carrier_code: 'road_freight',
    carrier_name: 'Hunter Road Freight',
    service: 2,
    service_code: 'HUNTER_SYD_2025',
    name: 'Hunter SYD Broers 20240920',
    version: 'SP-HUNTER-SYD-2025',
    status: 'ACTIVE',
    effective_status: 'Active',
    active_now: true,
    is_active: true,
    priority: 70,
    effective_from: '2024-09-20',
    rule_count: 97,
    zone_count: 16549,
    surcharge_count: 14,
    quote_channel_count: 1,
  },
  {
    id: 3,
    carrier: 3,
    carrier_code: '454',
    carrier_name: 'Direct Freight Express',
    service: 3,
    service_code: 'DFE_KILO_EX_MEL_2025',
    name: 'DFE EX MEL Feb 2025',
    version: 'DFE-EX-MEL-FEB-2025',
    status: 'ACTIVE',
    effective_status: 'Active',
    active_now: true,
    is_active: true,
    priority: 60,
    effective_from: '2025-02-01',
    rule_count: 169,
    zone_count: 16257,
    surcharge_count: 1460,
    quote_channel_count: 1,
  },
]

const skuRows = [
  {
    id: 1,
    sku: 'ACH-A11-BGX2',
    description: 'Oikiture 2x Armchair Velvet Accent Chair Full Product Name Visible',
    category: 'Living Room Seating',
    unit_weight_kg: '12.000',
    length_cm: '75.00',
    width_cm: '40.00',
    height_cm: '76.00',
    active: true,
    is_combo: false,
    combo_component_count: 0,
  },
  {
    id: 2,
    sku: 'COMBO-LIVING-2PC',
    description: 'Two piece living room combo bundle',
    category: 'Furniture Bundle',
    unit_weight_kg: '24.000',
    length_cm: '90.00',
    width_cm: '60.00',
    height_cm: '80.00',
    active: true,
    is_combo: true,
    combo_type_label: 'Combo',
    combo_component_count: 2,
  },
]

const skuLookup = (skuCode: string) => {
  const sku = skuRows.find((row) => row.sku.toLowerCase() === skuCode.toLowerCase()) || skuRows[0]
  return {
    sku,
    components: sku.is_combo
      ? [
          {
            combo_sku: sku.sku,
            component_sku: 'ACH-A11-BGX2',
            component_qty: '2',
            combo_title: sku.description,
            component_sku_snapshot: skuRows[0],
          },
        ]
      : [],
  }
}

const quoteRun = {
  id: 9001,
  run_type: 'MANUAL',
  status: 'COMPLETED',
  input_hash: 'e2e-manual',
  created_at: '2026-06-12T10:00:00+10:00',
  trace_logs: [],
  candidates: [
    {
      id: 101,
      rank: 2,
      provider_name: 'Hunter Road Freight',
      carrier_code: 'road_freight',
      carrier_name: 'Hunter Road Freight',
      service_code: 'HUNTER_MEL_2023',
      channel_code: 'pc_hunter_mel_2023',
      availability: 'AVAILABLE',
      not_available_reason: '',
      base_amount: '66.20',
      surcharge_amount: '10.00',
      fuel_amount: '21.27',
      adjustment_amount: '0.00',
      total_ex_gst: '97.47',
      gst_amount: '9.75',
      total_inc_gst: '107.22',
      charge_lines: [
        { id: 1, line_type: 'BASE', description: 'Hunter linehaul', amount_ex_gst: '66.20', gst_amount: '0.00', amount_inc_gst: '66.20' },
        { id: 2, line_type: 'FUEL', description: 'Fuel levy', amount_ex_gst: '21.27', gst_amount: '0.00', amount_inc_gst: '21.27' },
        { id: 3, line_type: 'GST', description: 'GST', amount_ex_gst: '0.00', gst_amount: '9.75', amount_inc_gst: '9.75' },
      ],
      trace_logs: [{ id: 1, event_type: 'CALCULATION', step: 'rate_card', message: 'Used Hunter MEL card', details_json: { rate_card: 'SP-HUNTER-MEL-2023' }, created_at: '2026-06-12T10:00:00+10:00' }],
      debug_breakdown: { chargeable_kg: '22', dest_zone: 'MEL' },
    },
    {
      id: 100,
      rank: 1,
      provider_name: 'Allied Express',
      carrier_code: '758',
      carrier_name: 'Allied Express',
      service_code: 'GRO_2023_MEL',
      channel_code: 'pc_allied_gro_2023_mel',
      availability: 'AVAILABLE',
      not_available_reason: '',
      base_amount: '42.50',
      surcharge_amount: '47.00',
      fuel_amount: '7.86',
      adjustment_amount: '-5.00',
      total_ex_gst: '92.36',
      gst_amount: '9.24',
      total_inc_gst: '101.60',
      charge_lines: [
        { id: 4, line_type: 'BASE', description: 'Base freight', amount_ex_gst: '42.50', gst_amount: '0.00', amount_inc_gst: '42.50' },
        { id: 5, line_type: 'FUEL', description: 'Fuel levy 18.5%', amount_ex_gst: '7.86', gst_amount: '0.00', amount_inc_gst: '7.86' },
        { id: 6, line_type: 'SURCHARGE', description: 'Residential surcharge', amount_ex_gst: '12.00', gst_amount: '0.00', amount_inc_gst: '12.00' },
        { id: 7, line_type: 'SURCHARGE', description: 'Oversize surcharge', amount_ex_gst: '35.00', gst_amount: '0.00', amount_inc_gst: '35.00' },
        { id: 8, line_type: 'SURCHARGE', description: 'OIS not triggered', amount_ex_gst: '0.00', gst_amount: '0.00', amount_inc_gst: '0.00' },
        { id: 9, line_type: 'ADJUSTMENT', description: 'Manual adjustment', amount_ex_gst: '-5.00', gst_amount: '0.00', amount_inc_gst: '-5.00' },
        { id: 10, line_type: 'GST', description: 'GST', amount_ex_gst: '0.00', gst_amount: '9.24', amount_inc_gst: '9.24' },
      ],
      trace_logs: [
        { id: 2, event_type: 'ELIGIBILITY', step: 'warehouse', message: 'Warehouse BG01 matched MEL origin', details_json: { warehouse: 'BG01', origin: 'MEL' }, created_at: '2026-06-12T10:00:00+10:00' },
        { id: 3, event_type: 'CALCULATION', step: 'zone', message: 'Matched destination zone', details_json: { dest_zone: 'V01', chargeable_kg: '28' }, created_at: '2026-06-12T10:00:00+10:00' },
      ],
      debug_breakdown: { dead_kg: '12', cubic_kg: '28', chargeable_kg: '28', dest_zone: 'V01' },
    },
    {
      id: 102,
      rank: 3,
      provider_name: 'Direct Freight Express',
      carrier_code: '454',
      carrier_name: 'Direct Freight Express',
      service_code: 'DFE_KILO_EX_MEL_2025',
      channel_code: 'dfe_ex_mel_2025',
      availability: 'NOT_AVAILABLE',
      not_available_reason: 'item_exceeds_profile_limit',
      base_amount: '0.00',
      surcharge_amount: '0.00',
      fuel_amount: '0.00',
      adjustment_amount: '0.00',
      total_ex_gst: '0.00',
      gst_amount: '0.00',
      total_inc_gst: '0.00',
      charge_lines: [],
      trace_logs: [{ id: 4, event_type: 'NOT_AVAILABLE', step: 'profile', message: 'DFE profile limit failed', details_json: { reason: 'oversize' }, created_at: '2026-06-12T10:00:00+10:00' }],
      debug_breakdown: {},
    },
  ],
}

const orderLookup = [
  {
    id: 71,
    label: 'O1965424994346143745 / DP000832714',
    order_no: 'O1965424994346143745',
    order_date: '2026-04-11',
    order_refs: { platform_order_no: 'DP000832714' },
    platform_code: 'PI2022080502320043121506',
    platform_name: 'Fantastic',
    warehouse_code: 'BGS1',
    warehouse_name: 'BGS1',
    tracking_numbers: ['8566090069989'],
    shipping_option: 'delivery',
    source_estimated_freight: '43.55',
    postage_shipping_estimated_amount: '43.55',
    actual_carrier: 'Allied Express',
    destination: { state: 'NSW', suburb: 'WOY WOY', postcode: '2256', country: 'AU' },
    destination_label: 'WOY WOY, NSW 2256',
    sales_items: [{ source: 'sales', sku: 'MAT-A18-M907-Q', description: 'Mattress Queen', qty: '1', unit_weight_kg: '20', length_cm: '160', width_cm: '35', height_cm: '35', category: 'Mattress' }],
    shipment_items: [{ source: 'shipment', sku: 'MAT-A18-M907-Q', qty: '1', tracking_no: '8566090069989', carrier_name: 'Allied Express', carrier_channel: 'GRO', warehouse_code: 'BGS1' }],
    quote_items: [{ source: 'sales', sku: 'MAT-A18-M907-Q', qty: '1', unit_weight_kg: '20', length_cm: '160', width_cm: '35', height_cm: '35', category: 'Mattress', tracking_numbers: ['8566090069989'] }],
    quote_item_source: 'sales',
    lsp_quote: {
      order_code: 'BG05SO05260319000526-1',
      shipment_code: 'BG050003811783-1',
      reference_no: 'O1965424994346143745',
      warehouse_code: 'BGS1',
      selected_total: '47.90',
      options: [
        { id: 1, option_index: 1, agent_name: 'Broers', carrier_name: 'Allied Express', can_shipping: true, shipping_cost: '47.90', carrier_shipping_cost: '47.90' },
        { id: 2, option_index: 2, agent_name: 'EIZ', carrier_name: 'Hunter Express', can_shipping: true, shipping_cost: '58.20', carrier_shipping_cost: '58.20' },
      ],
      agent_breakdown: [],
    },
  },
]

const invoiceBatches = [
  {
    id: 244,
    name: 'Allied Express / BROGRO',
    invoice_source_code: 'INV_SRC_C6D1C7A0941D',
    invoice_source_name: 'Allied Express / BROGRO',
    carrier_code: '758',
    carrier_name: 'Allied Express',
    carrier_service_code: 'GRO_2023_MEL',
    carrier_service_name: 'Allied GRO 2023 Melbourne',
    status: 'COMPLETED',
    total_rows: 5000,
    matched_rows: 206,
    exception_rows: 12,
    created_at: '2026-06-12T11:00:00+10:00',
  },
]

const invoiceItems = [
  {
    id: 501,
    order_no: 'O1965424994346143745',
    consignment_no: '8566090069989',
    invoice_no: 'INV-ALLIED-001',
    carrier_code: '758',
    carrier_name: 'Allied Express',
    carrier_service_code: 'GRO_2023_MEL',
    carrier_service_name: 'Allied GRO 2023 Melbourne',
    invoice_source_code: 'INV_SRC_C6D1C7A0941D',
    invoice_source_name: 'Allied Express / BROGRO',
    estimated_freight: '58.20',
    system_estimated_freight: '61.19',
    actual_freight: '65.70',
    variance_amount: '1.68',
    variance_percent: '2.60',
    system_variance_amount: '4.51',
    system_variance_percent: '7.40',
    match_status: 'MATCHED',
    variance_type: 'OVERCHARGE',
    dispute_recommended: true,
    reason: 'Actual exceeds ERP estimate',
    system_estimate_reason: 'system_quote_matched_allied_carrier',
  },
]

const auditHunterResult = {
  id: 701,
  row: 700,
  quote_channel: 11,
  quote_channel_code: 'pc_hunter_syd_2025',
  quote_candidate: 7101,
  quote_candidate_id: 7101,
  carrier: 2,
  carrier_service: 2,
  carrier_key: 'hunter',
  carrier_name: 'Hunter Road Freight',
  service_name: 'Hunter SYD Broers 20240920',
  provider_type: 'TABLE',
  availability: 'AVAILABLE',
  not_available_reason: '',
  base_amount: '42.30',
  surcharge_amount: '5.00',
  fuel_amount: '8.32',
  adjustment_amount: '0.00',
  gst_amount: '5.56',
  total_inc_gst: '61.18',
  variance_to_erp: '-2.84',
  variance_to_invoice: '-4.52',
  rank: 1,
  raw_payload: {
    provider_name: 'Hunter Road Freight',
    channel_code: 'pc_hunter_syd_2025',
    rate_card: 'SP-HUNTER-SYD-2025',
    debug_breakdown: { calculation_mode: 'CONSIGNMENT_AGGREGATED_TO_ORDER', erp_estimate_scope: 'ORDER', quoted_tracking_groups: 1 },
    components: [
      {
        tracking: '8566090069989',
        quote_run_id: 8801,
        quote_candidate_id: 7101,
        availability: 'AVAILABLE',
        total_inc_gst: '61.18',
        debug_breakdown: { chargeable_kg: '28', cubic_kg: '24', dest_zone: 'SYD' },
        items: [{ sku: 'MAT-A18-M907-Q', qty: '1', unit_weight_kg: '20', length_cm: '160', width_cm: '35', height_cm: '35', actual_kg: '20', cubic_kg: '19.60', cubic_factor: '250', category: 'Mattress', calculation_source: 'sales' }],
      },
    ],
    charge_lines: [
      { tracking: '8566090069989', type: 'BASE', description: 'Hunter linehaul', amount_ex_gst: '42.30', gst_amount: '0.00', amount_inc_gst: '42.30' },
      { tracking: '8566090069989', type: 'FUEL', description: 'Fuel levy', amount_ex_gst: '8.32', gst_amount: '0.00', amount_inc_gst: '8.32' },
      { tracking: '8566090069989', type: 'GST', description: 'GST', amount_ex_gst: '0.00', gst_amount: '5.56', amount_inc_gst: '5.56' },
    ],
  },
}

const auditRows = [
  {
    id: 700,
    source_system: 'invoiceReader.tracking_reconciliation',
    source_external_id: 'e2e-audit-700',
    calculation_mode: 'CONSIGNMENT',
    invoice_reconciliation_item: 501,
    erp_shipment_snapshot: 601,
    quote_run: 8801,
    order_no: 'O1965424994346143745',
    tracking_no: '8566090069989',
    platform_code: 'PI2022080502320043121506',
    platform_name: 'Fantastic',
    warehouse_code: 'BGS1',
    order_date: '2026-04-11',
    suburb: 'WOY WOY',
    postcode: '2256',
    state: 'NSW',
    erp_estimated_freight: '58.20',
    invoice_actual_freight: '65.70',
    item_count: 1,
    total_qty: '1',
    status: 'COMPLETED',
    error_message: '',
    raw_payload: {},
    results: [
      auditHunterResult,
      {
        ...auditHunterResult,
        id: 702,
        carrier_key: 'allied',
        carrier_name: 'Allied Express',
        service_name: 'Allied GRO 2023 Sydney',
        quote_channel_code: 'pc_allied_gro_2023_syd',
        total_inc_gst: '72.61',
        variance_to_invoice: '6.91',
        raw_payload: { ...auditHunterResult.raw_payload, rate_card: 'SP-ALLIED-GRO-SYD-2023', channel_code: 'pc_allied_gro_2023_syd' },
      },
      {
        ...auditHunterResult,
        id: 703,
        carrier_key: 'direct_freight',
        carrier_name: 'Direct Freight Express',
        service_name: 'DFE EX SYD Feb 2025',
        quote_channel_code: 'dfe_ex_syd_2025',
        total_inc_gst: '33.44',
        variance_to_invoice: '-32.26',
        raw_payload: { ...auditHunterResult.raw_payload, rate_card: 'DFE-EX-SYD-FEB-2025', channel_code: 'dfe_ex_syd_2025' },
      },
    ],
    best_results: {
      hunter: auditHunterResult,
      allied: {
        ...auditHunterResult,
        id: 702,
        carrier_key: 'allied',
        carrier_name: 'Allied Express',
        service_name: 'Allied GRO 2023 Sydney',
        quote_channel_code: 'pc_allied_gro_2023_syd',
        total_inc_gst: '72.61',
        variance_to_invoice: '6.91',
      },
      direct_freight: {
        ...auditHunterResult,
        id: 703,
        carrier_key: 'direct_freight',
        carrier_name: 'Direct Freight Express',
        service_name: 'DFE EX SYD Feb 2025',
        quote_channel_code: 'dfe_ex_syd_2025',
        total_inc_gst: '33.44',
        variance_to_invoice: '-32.26',
      },
    },
    created_at: '2026-06-12T12:00:00+10:00',
    updated_at: '2026-06-12T12:00:00+10:00',
  },
]

const quoteChannels = [
  { id: 1, code: 'pc_allied_gro_2023_mel', name: 'Allied GRO 2023 Melbourne', carrier: 1, carrier_code: '758', carrier_name: 'Allied Express', service: 1, service_code: 'GRO_2023_MEL', provider_type: 'TABLE', calculator_key: 'allied', enabled: true, priority: 10, rate_card: 1, agent: null },
  { id: 2, code: 'pc_hunter_syd_2025', name: 'Hunter SYD Broers 20240920', carrier: 2, carrier_code: 'road_freight', carrier_name: 'Hunter Road Freight', service: 2, service_code: 'HUNTER_SYD_2025', provider_type: 'TABLE', calculator_key: 'hunter', enabled: true, priority: 20, rate_card: 2, agent: null },
]

const destinations = [{ suburb: 'SOUTH MELBOURNE', state: 'VIC', postcode: '3205', label: 'SOUTH MELBOURNE, VIC 3205', rate_card_count: 7 }]

const filterBySearch = <T extends Record<string, unknown>>(rows: T[], search: string) => {
  const keyword = search.trim().toLowerCase()
  if (!keyword) return rows
  return rows.filter((row) => JSON.stringify(row).toLowerCase().includes(keyword))
}

async function fulfillJson(route: Route, payload: JsonValue | Record<string, unknown>, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

export async function setupFreightMockApi(page: Page): Promise<FreightMockState> {
  const state: FreightMockState = { searches: [] }

  page.on('pageerror', (error) => {
    console.log(`[pageerror] ${error.message}`)
  })
  page.on('console', (message) => {
    const text = message.text()
    if (message.type() === 'error' && !text.startsWith('Warning: [antd:')) console.log(`[console:error] ${text}`)
  })

  await page.addInitScript(() => {
    window.localStorage.setItem('freight_access_token', 'e2e-token')
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      return route.continue()
    }
    const path = url.pathname.replace(/^\/api/, '') || '/'
    const method = request.method()
    const search = url.searchParams.get('search') || ''
    if (search) state.searches.push({ path, search })

    if (method === 'GET' && path === '/auth/me') return fulfillJson(route, user)
    if (method === 'GET' && path === '/platforms/') return fulfillJson(route, paginated(filterBySearch(platforms, search)))
    if (method === 'GET' && path === '/warehouses/') return fulfillJson(route, paginated(filterBySearch(warehouses, search)))
    if (method === 'GET' && path === '/carriers/') return fulfillJson(route, paginated(filterBySearch(carriers, search)))
    if (method === 'GET' && path === '/carrier-services/') return fulfillJson(route, paginated(filterBySearch(services, search)))
    if (method === 'GET' && path === '/rate-cards/') return fulfillJson(route, paginated(filterBySearch(rateCards, search)))
    if (method === 'GET' && path === '/rate-zones/destinations/') return fulfillJson(route, destinations)
    if (method === 'GET' && path === '/rate-zones/') return fulfillJson(route, paginated([{ id: 1, carrier_name: 'Allied Express', rate_card_version: 'SP-ALLIED-GRO-MEL-2023', origin_zone: 'MEL', dest_zone: 'V01', state: 'VIC', suburb: 'SOUTH MELBOURNE', postcode: '3205', deliverable: true }]))
    if (method === 'GET' && path === '/rate-rules/') return fulfillJson(route, paginated([{ id: 1, carrier_name: 'Allied Express', rate_card_version: 'SP-ALLIED-GRO-MEL-2023', service_code: 'GRO_2023_MEL', from_zone: 'MEL', to_zone: 'V01', weight_min_kg: '0', weight_max_kg: null, basic_charge: '42.50', per_kg: '0.25', minimum_charge: '12.00', rule_type: 'LINEHAUL', priority: 1 }]))
    if (method === 'GET' && path === '/surcharge-rules/') return fulfillJson(route, paginated([{ id: 1, carrier_name: 'Allied Express', rate_card_version: 'SP-ALLIED-GRO-MEL-2023', code: 'FS', rule_name: 'Fuel levy', match_dimension: 'ALWAYS', min_threshold: null, max_threshold: null, fee_amount: '0.00', ratio: '0.185', active: true }]))
    if (method === 'GET' && path === '/adjustment-rules/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/quote-channels/') return fulfillJson(route, paginated(quoteChannels))
    if (method === 'GET' && path === '/warehouse-platforms/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/warehouse-carriers/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/platform-carriers/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/agents/') return fulfillJson(route, paginated([{ id: 1, code: 'BROERS', name: 'Broers', agent_type: 'RATE_OWNER', active: true, supports_api: false, maintains_rate_cards: true }]))
    if (method === 'GET' && path === '/users/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/audit-logs/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/import-jobs/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/quote-runs/') return fulfillJson(route, paginated([quoteRun]))
    if (method === 'GET' && path === '/skus/') return fulfillJson(route, paginated(filterBySearch(skuRows, search)))
    if (method === 'GET' && path === '/skus/combo-master/') return fulfillJson(route, paginated([skuRows[1]]))
    if (method === 'GET' && path === '/skus/lookup/') return fulfillJson(route, skuLookup(url.searchParams.get('sku') || 'ACH-A11-BGX2'))
    if (method === 'GET' && path === '/historical-orders/order-lookup/') return fulfillJson(route, orderLookup)
    if (method === 'GET' && path === '/historical-orders/') return fulfillJson(route, paginated([]))
    if (method === 'GET' && path === '/lsp-api-quotes/') return fulfillJson(route, paginated([]))

    if (method === 'POST' && path === '/quotes/manual') {
      state.lastQuotePayload = request.postDataJSON() as Record<string, unknown>
      return fulfillJson(route, quoteRun)
    }

    if (method === 'GET' && path === '/invoice-reconciliation-batches/') return fulfillJson(route, paginated(filterBySearch(invoiceBatches, search)))
    if (method === 'GET' && path === '/invoice-reconciliation-items/') return fulfillJson(route, paginated(filterBySearch(invoiceItems, search)))
    if (method === 'POST' && path === '/invoice-reconciliation-batches/sync-from-sqlserver/') {
      return fulfillJson(route, {
        import_job: { success_rows: 300, error_rows: 0 },
        batches: invoiceBatches,
      })
    }
    const exportMatch = path.match(/^\/invoice-reconciliation-batches\/(\d+)\/export\/$/)
    if (method === 'GET' && exportMatch) {
      state.lastInvoiceExport = { batchId: exportMatch[1], scope: url.searchParams.get('scope') || 'all' }
      return route.fulfill({
        status: 200,
        contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: { 'content-disposition': 'attachment; filename="invoice-reconciliation-e2e.xlsx"' },
        body: Buffer.from('e2e-export'),
      })
    }

    if (method === 'GET' && path === '/freight-audit-rows/') return fulfillJson(route, paginated(filterBySearch(auditRows, search)))
    if (method === 'POST' && path === '/freight-audit-rows/build-from-reconciliation/') {
      state.lastAuditBuildPayload = request.postDataJSON() as Record<string, unknown>
      return fulfillJson(route, { output: 'built 1 e2e row' })
    }

    if (method === 'POST' || method === 'PATCH' || method === 'DELETE') return fulfillJson(route, { ok: true })
    return fulfillJson(route, paginated([]))
  })

  return state
}
