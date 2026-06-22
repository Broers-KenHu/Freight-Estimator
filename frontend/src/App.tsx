import {
  EyeOutlined,
  LogoutOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Avatar, Button, ConfigProvider, Layout, Menu, Space, Tag, Typography, message, theme } from 'antd'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import './styles.css'
import { api, listResource } from './api/client'
import { clearAccessToken, getAccessToken } from './auth/session'
import { ResourceTable, StatusTag } from './components/ResourceTable'
import { PlatformDetailDrawer } from './components/PlatformDetailDrawer'
import { boolRender, carrierNameRender, flag, num, selectField, text } from './config/fieldFactories'
import { buildVisibleMenuItems, type MenuKey } from './config/menuItems'
import { AccessManagement } from './pages/AccessManagement'
import { Dashboard } from './pages/Dashboard'
import { FreightAuditMatrix } from './pages/FreightAuditMatrix'
import { HistoricalOrders } from './pages/HistoricalOrders'
import { InvoiceReconciliation } from './pages/InvoiceReconciliation'
import { LoginPage } from './pages/LoginPage'
import { LspApiQuotes } from './pages/LspApiQuotes'
import { ManualQuote } from './pages/ManualQuote'
import { PlatformCarriers } from './pages/PlatformCarriers'
import { QuoteChannels } from './pages/QuoteChannels'
import { QuoteRuns } from './pages/QuoteRuns'
import { RateCards } from './pages/RateCards'
import { SKUMaster } from './pages/SKUMaster'
import type { Carrier, CarrierService, Platform, RateCard, UserProfile, Warehouse } from './types'

const { Header, Sider, Content } = Layout
const ACCESS_REQUEST_TIMEOUT_MS = 8000

function MasterSyncButton({ endpoint, label, invalidateKey }: { endpoint: string; label: string; invalidateKey: string }) {
  const [messageApi, contextHolder] = message.useMessage()
  const queryClient = useQueryClient()
  const syncMutation = useMutation({
    mutationFn: async () => (await api.post(endpoint, {})).data,
    onSuccess: (data) => {
      const job = data.import_job
      messageApi.success(`${label} completed: ${job.success_rows} synced, ${job.error_rows} errors`)
      queryClient.invalidateQueries({ queryKey: [invalidateKey] })
      queryClient.invalidateQueries({ queryKey: ['/import-jobs/'] })
    },
    onError: () => messageApi.error(`${label} failed`),
  })

  return (
    <>
      {contextHolder}
      <Button icon={<SyncOutlined />} loading={syncMutation.isPending} onClick={() => syncMutation.mutate()}>
        {label}
      </Button>
    </>
  )
}

function ResourcePage({ page }: { page: MenuKey }): ReactNode {
  const { data: platforms = [] } = useQuery({ queryKey: ['/platforms/'], queryFn: () => listResource<Platform>('/platforms/') })
  const { data: carriers = [] } = useQuery({ queryKey: ['/carriers/'], queryFn: () => listResource<Carrier>('/carriers/') })
  const { data: services = [] } = useQuery({ queryKey: ['/carrier-services/'], queryFn: () => listResource<CarrierService>('/carrier-services/') })
  const { data: warehouses = [] } = useQuery({ queryKey: ['/warehouses/'], queryFn: () => listResource<Warehouse>('/warehouses/') })
  const { data: rateCards = [] } = useQuery({ queryKey: ['/rate-cards/'], queryFn: () => listResource<RateCard>('/rate-cards/') })
  const [platformDetailId, setPlatformDetailId] = useState<number>()
  const [platformDetailOpen, setPlatformDetailOpen] = useState(false)

  const platformOptions = platforms.map((platform) => ({ value: platform.id, label: `${platform.code} - ${platform.name}` }))
  const carrierOptions = carriers.map((carrier) => ({ value: carrier.id, label: carrier.name || carrier.code }))
  const serviceOptions = services.map((service) => ({
    value: service.id,
    label: `${service.carrier_name || service.carrier_code} / ${service.name || service.code}`,
  }))
  const warehouseOptions = warehouses.map((warehouse) => ({ value: warehouse.id, label: `${warehouse.code} - ${warehouse.name}` }))
  const rateCardOptions = rateCards.map((card) => ({ value: card.id, label: `${card.carrier_name || card.carrier_code} ${card.version} - ${card.name}` }))
  const carrierTypeOptions = ['TABLE', 'API', 'HYBRID'].map((value) => ({ label: value, value }))
  const agentTypeOptions = [
    { label: 'LSP Agent', value: 'LSP' },
    { label: 'API Agent', value: 'API' },
    { label: 'Rate owner', value: 'RATE_OWNER' },
    { label: 'Other', value: 'OTHER' },
  ]
  const platformTypeOptions = ['ECOMMERCE', 'MARKETPLACE', 'MANUAL', 'API'].map((value) => ({ label: value, value }))
  const platformRoleOptions = [
    { label: 'Sales platform', value: 'SALES' },
    { label: 'Carrier quote platform', value: 'CARRIER_QUOTE' },
  ]
  const rateRuleTypeOptions = ['LINEHAUL', 'PER_ITEM', 'FALLBACK'].map((value) => ({ label: value, value }))
  const surchargeDimensionOptions = ['WEIGHT', 'LENGTH', 'BORDER', 'CUBIC', 'ALWAYS'].map((value) => ({ label: value, value }))
  const adjustmentActionOptions = ['ADD_FIXED', 'SUBTRACT_FIXED', 'ADD_PERCENT', 'OVERRIDE', 'MIN_CHARGE', 'CAP', 'BLOCK_SERVICE'].map((value) => ({ label: value, value }))

  const pages: Partial<Record<MenuKey, ReactNode>> = {
    quoteRuns: (
      <ResourceTable
        title="Quote Runs"
        endpoint="/quote-runs/"
        fields={[]}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 80 },
          { title: 'Type', dataIndex: 'run_type', width: 130 },
          { title: 'Status', dataIndex: 'status', width: 130, render: (value) => <StatusTag value={value} /> },
          { title: 'Input hash', dataIndex: 'input_hash', ellipsis: true },
          { title: 'Created', dataIndex: 'created_at', width: 200 },
        ]}
      />
    ),
    platforms: (
      <>
        <ResourceTable
          title="Platforms"
          endpoint="/platforms/"
          extraActions={<MasterSyncButton endpoint="/platforms/sync-from-erp/" label="Sync ERP Platforms" invalidateKey="/platforms/" />}
          filters={[
            { name: 'active', label: 'Active', kind: 'boolean' },
            { name: 'platform_role', label: 'Role', kind: 'select', options: platformRoleOptions },
            { name: 'platform_type', label: 'Internal type', kind: 'select', options: platformTypeOptions },
          ]}
          fields={[
            text('code', 'Code', true),
            text('name', 'Name', true),
            text('company', 'Company'),
            selectField('platform_role', 'Role', platformRoleOptions, true),
            selectField('platform_type', 'Internal type', platformTypeOptions),
            flag('active', 'Active'),
            selectField('default_origin_warehouse', 'Default warehouse', warehouseOptions),
          ]}
          columns={[
            { title: 'Code', dataIndex: 'code', width: 140 },
            { title: 'Name', dataIndex: 'name', width: 180 },
            { title: 'Role', dataIndex: 'platform_role', width: 150 },
            { title: 'Company', dataIndex: 'company', width: 180, render: (value) => value || '-' },
            {
              title: 'ERP Type',
              dataIndex: 'source_platform_type_name_en',
              width: 160,
              render: (value, record: Record<string, unknown>) => value || record.source_platform_type_code || '-',
            },
            { title: 'ERP Type CN', dataIndex: 'source_platform_type_name_zh', width: 140, render: (value) => value || '-' },
            { title: 'Group', dataIndex: 'platform_group_name_en', width: 120, render: (value) => value || '-' },
            { title: 'Internal', dataIndex: 'platform_type', width: 120 },
            { title: 'Source updated', dataIndex: 'external_updated_at', width: 180, render: (value) => value || '-' },
            { title: 'Active', dataIndex: 'active', width: 100, render: boolRender },
          ]}
          rowActions={(record) => (
            <Button
              aria-label="Detail"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => {
                setPlatformDetailId(record.id)
                setPlatformDetailOpen(true)
              }}
            />
          )}
        />
        <PlatformDetailDrawer platformId={platformDetailId} open={platformDetailOpen} onClose={() => setPlatformDetailOpen(false)} />
      </>
    ),
    carriers: (
      <ResourceTable
        title="Carriers"
        endpoint="/carriers/"
        fields={[text('name', 'Name', true), selectField('carrier_type', 'Carrier type', carrierTypeOptions), flag('support_api', 'Supports API'), flag('active', 'Active'), { name: 'notes', label: 'Notes', kind: 'textarea' }]}
        columns={[
          { title: 'Name', dataIndex: 'name', width: 240 },
          { title: 'System code', dataIndex: 'code', width: 140 },
          { title: 'Type', dataIndex: 'carrier_type', width: 120 },
          { title: 'API', dataIndex: 'support_api', width: 100, render: boolRender },
          { title: 'LSP Agent', dataIndex: 'lsp_agent_code', width: 110, render: (value) => value || '-' },
          { title: 'LSP Channel', dataIndex: 'lsp_channel_code', width: 130, render: (value) => value || '-' },
          { title: 'Rate Rows', dataIndex: 'active_rate_rows', width: 110, align: 'right', render: (value) => value || 0 },
          { title: 'Platform Rates', dataIndex: 'active_quote_rate_rows', width: 130, align: 'right', render: (value) => value || 0 },
          { title: 'API Accts', dataIndex: 'active_api_accounts', width: 100, align: 'right', render: (value) => value || 0 },
          { title: 'Source updated', dataIndex: 'external_updated_at', width: 190, render: (value) => value || '-' },
          { title: 'Active', dataIndex: 'active', width: 120, render: boolRender },
        ]}
      />
    ),
    agents: (
      <ResourceTable
        title="Agents"
        endpoint="/agents/"
        extraActions={<MasterSyncButton endpoint="/agents/sync-from-lsp/" label="Sync LSP Agents" invalidateKey="/agents/" />}
        filters={[
          { name: 'active', label: 'Active', kind: 'boolean' },
          { name: 'agent_type', label: 'Agent type', kind: 'select', options: agentTypeOptions },
          { name: 'supports_api', label: 'Supports API', kind: 'boolean' },
          { name: 'maintains_rate_cards', label: 'Maintains rate cards', kind: 'boolean' },
        ]}
        fields={[
          text('name', 'Name', true),
          selectField('agent_type', 'Agent type', agentTypeOptions, true),
          flag('supports_api', 'Supports API'),
          flag('maintains_rate_cards', 'Maintains rate cards'),
          text('lsp_consign_agent_id', 'LSP consign agent'),
          flag('active', 'Active'),
          { name: 'notes', label: 'Notes', kind: 'textarea' },
        ]}
        columns={[
          { title: 'Name', dataIndex: 'name', width: 220 },
          { title: 'System code', dataIndex: 'code', width: 130 },
          { title: 'Type', dataIndex: 'agent_type', width: 120 },
          { title: 'API', dataIndex: 'supports_api', width: 100, render: boolRender },
          { title: 'Rate owner', dataIndex: 'maintains_rate_cards', width: 120, render: boolRender },
          { title: 'LSP status', dataIndex: 'lsp_status_code', width: 110, render: (value) => value || '-' },
          { title: 'LSP channels', dataIndex: 'channel_count', width: 120, align: 'right', render: (value) => value || 0 },
          { title: 'Carriers', dataIndex: 'carrier_count', width: 100, align: 'right', render: (value) => value || 0 },
          { title: 'Source updated', dataIndex: 'external_updated_at', width: 190, render: (value) => value || '-' },
          { title: 'Active', dataIndex: 'active', width: 110, render: boolRender },
        ]}
      />
    ),
    carrierServices: (
      <ResourceTable
        title="Carrier Services"
        endpoint="/carrier-services/"
        fields={[selectField('carrier', 'Carrier', carrierOptions, true), text('code', 'Code', true), text('name', 'Name', true), text('service_level', 'Service level'), flag('active', 'Active')]}
        columns={[
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Code', dataIndex: 'code', width: 120 },
          { title: 'Name', dataIndex: 'name' },
          { title: 'Active', dataIndex: 'active', width: 120, render: boolRender },
        ]}
      />
    ),
    platformCarriers: (
      <PlatformCarriers />
    ),
    warehouses: (
      <ResourceTable
        title="Warehouses"
        endpoint="/warehouses/"
        extraActions={<MasterSyncButton endpoint="/warehouses/sync-from-wms/" label="Sync WMS Warehouses" invalidateKey="/warehouses/" />}
        fields={[
          text('code', 'Code', true),
          text('name', 'Name', true),
          text('address', 'Address'),
          text('address2', 'Address 2'),
          text('suburb', 'Suburb'),
          text('postcode', 'Postcode'),
          text('state', 'State'),
          text('region', 'Region'),
          text('default_origin_zone', 'Default origin zone'),
          flag('active', 'Active'),
        ]}
        columns={[
          { title: 'Code', dataIndex: 'code', width: 140 },
          { title: 'Name', dataIndex: 'name', width: 180 },
          { title: 'Address', dataIndex: 'address', width: 260, ellipsis: true },
          { title: 'Suburb', dataIndex: 'suburb', width: 150 },
          { title: 'Postcode', dataIndex: 'postcode', width: 120 },
          { title: 'State', dataIndex: 'state', width: 90 },
          { title: 'Region', dataIndex: 'region', width: 100, render: (value) => value || '-' },
          { title: 'Contact', dataIndex: 'contact_name', width: 140, render: (value) => value || '-' },
          { title: 'Phone', dataIndex: 'telephone', width: 140, render: (value) => value || '-' },
          { title: 'Source updated', dataIndex: 'external_updated_at', width: 190, render: (value) => value || '-' },
          { title: 'Active', dataIndex: 'active', width: 120, render: boolRender },
        ]}
      />
    ),
    warehousePlatforms: (
      <ResourceTable
        title="Warehouse Platforms"
        endpoint="/warehouse-platforms/"
        fields={[selectField('warehouse', 'Warehouse', warehouseOptions, true), selectField('platform', 'Platform', platformOptions, true), flag('enabled', 'Enabled'), num('priority', 'Priority'), flag('is_default', 'Default'), text('valid_from', 'Valid from YYYY-MM-DD'), text('valid_to', 'Valid to YYYY-MM-DD'), { name: 'notes', label: 'Notes', kind: 'textarea' }]}
        columns={[
          { title: 'Warehouse', dataIndex: 'warehouse_code', width: 150 },
          { title: 'Warehouse name', dataIndex: 'warehouse_name', width: 180, render: (value) => value || '-' },
          { title: 'Platform', dataIndex: 'platform_code', width: 150 },
          { title: 'Platform name', dataIndex: 'platform_name', width: 180, render: (value) => value || '-' },
          { title: 'Default', dataIndex: 'is_default', width: 110, render: boolRender },
          { title: 'Priority', dataIndex: 'priority', width: 100 },
          { title: 'Enabled', dataIndex: 'enabled', width: 120, render: boolRender },
        ]}
      />
    ),
    warehouseCarriers: (
      <ResourceTable
        title="Warehouse Carriers"
        endpoint="/warehouse-carriers/"
        fields={[selectField('warehouse', 'Warehouse', warehouseOptions, true), selectField('carrier', 'Carrier', carrierOptions, true), selectField('service', 'Service', serviceOptions, true), flag('enabled', 'Enabled'), text('account_code', 'Account code'), text('origin_zone', 'Origin zone'), text('cut_off_time', 'Cut off time HH:MM'), num('max_weight_kg', 'Max weight kg'), num('max_volume_m3', 'Max volume m3'), { name: 'notes', label: 'Notes', kind: 'textarea' }]}
        columns={[
          { title: 'Warehouse', dataIndex: 'warehouse_code', width: 150 },
          { title: 'Warehouse name', dataIndex: 'warehouse_name', width: 180, render: (value) => value || '-' },
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Service', dataIndex: 'service_name', width: 180, render: (value, record: Record<string, unknown>) => value || record.service_code || '-' },
          { title: 'Origin zone', dataIndex: 'origin_zone', width: 130 },
          { title: 'Cut off', dataIndex: 'cut_off_time', width: 100, render: (value) => value || '-' },
          { title: 'Max kg', dataIndex: 'max_weight_kg', width: 100, render: (value) => value || '-' },
          { title: 'Max m3', dataIndex: 'max_volume_m3', width: 100, render: (value) => value || '-' },
          { title: 'Enabled', dataIndex: 'enabled', width: 120, render: boolRender },
        ]}
      />
    ),
    rateZones: (
      <ResourceTable
        title="Rate Zones"
        endpoint="/rate-zones/"
        filters={[
          { name: 'rate_card', label: 'Rate card', kind: 'select', options: rateCardOptions },
          { name: 'dest_zone', label: 'Dest zone' },
          { name: 'state', label: 'State' },
          { name: 'postcode', label: 'Postcode' },
          { name: 'deliverable', label: 'Deliverable', kind: 'boolean' },
        ]}
        fields={[selectField('rate_card', 'Rate card', rateCardOptions, true), text('origin_zone', 'Origin zone'), text('dest_zone', 'Destination zone', true), text('state', 'State'), text('suburb', 'Suburb'), text('postcode', 'Postcode'), text('postcode_from', 'Postcode from'), text('postcode_to', 'Postcode to'), flag('deliverable', 'Deliverable'), { name: 'raw_payload', label: 'Raw payload JSON', kind: 'json' }]}
        columns={[
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Rate card', dataIndex: 'rate_card_version', width: 140 },
          { title: 'Origin zone', dataIndex: 'origin_zone', width: 120 },
          { title: 'Dest zone', dataIndex: 'dest_zone', width: 120 },
          { title: 'State', dataIndex: 'state', width: 90 },
          { title: 'Suburb', dataIndex: 'suburb', width: 170 },
          { title: 'Postcode', dataIndex: 'postcode', width: 110 },
          { title: 'Range', width: 150, render: (_, record: Record<string, unknown>) => `${record.postcode_from || '-'} / ${record.postcode_to || '-'}` },
          { title: 'Deliverable', dataIndex: 'deliverable', width: 120, render: boolRender },
        ]}
      />
    ),
    rateRules: (
      <ResourceTable
        title="Rate Rules"
        endpoint="/rate-rules/"
        filters={[
          { name: 'rate_card', label: 'Rate card', kind: 'select', options: rateCardOptions },
          { name: 'service', label: 'Service', kind: 'select', options: serviceOptions },
          { name: 'to_zone', label: 'To zone' },
          { name: 'rule_type', label: 'Rule type', kind: 'select', options: rateRuleTypeOptions },
        ]}
        fields={[selectField('rate_card', 'Rate card', rateCardOptions, true), selectField('service', 'Service', serviceOptions), text('from_zone', 'From zone'), text('to_zone', 'To zone'), text('state', 'State'), text('suburb', 'Suburb'), text('postcode', 'Postcode'), num('weight_min_kg', 'Min kg'), num('weight_max_kg', 'Max kg'), num('basic_charge', 'Basic charge'), num('per_kg', 'Per kg'), num('minimum_charge', 'Minimum'), num('maximum_charge', 'Maximum'), selectField('rule_type', 'Rule type', rateRuleTypeOptions), num('priority', 'Priority'), { name: 'raw_payload', label: 'Raw payload JSON', kind: 'json' }]}
        columns={[
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Rate card', dataIndex: 'rate_card_version', width: 130 },
          { title: 'Service', dataIndex: 'service_code', width: 100, render: (value) => value || '-' },
          { title: 'From zone', dataIndex: 'from_zone', width: 110 },
          { title: 'To zone', dataIndex: 'to_zone', width: 110 },
          { title: 'Min kg', dataIndex: 'weight_min_kg', width: 100 },
          { title: 'Max kg', dataIndex: 'weight_max_kg', width: 100, render: (value) => value || '-' },
          { title: 'Basic', dataIndex: 'basic_charge', width: 100 },
          { title: 'Per kg', dataIndex: 'per_kg', width: 100 },
          { title: 'Minimum', dataIndex: 'minimum_charge', width: 110 },
          { title: 'Rule type', dataIndex: 'rule_type', width: 120 },
          { title: 'Priority', dataIndex: 'priority', width: 100 },
        ]}
      />
    ),
    surcharges: (
      <ResourceTable
        title="Surcharges"
        endpoint="/surcharge-rules/"
        filters={[
          { name: 'carrier', label: 'Carrier', kind: 'select', options: carrierOptions },
          { name: 'rate_card', label: 'Rate card', kind: 'select', options: rateCardOptions },
          { name: 'code', label: 'Code' },
          { name: 'active', label: 'Active', kind: 'boolean' },
          { name: 'match_dimension', label: 'Dimension', kind: 'select', options: surchargeDimensionOptions },
        ]}
        fields={[selectField('carrier', 'Carrier', carrierOptions), selectField('rate_card', 'Rate card', rateCardOptions), text('code', 'Code', true), text('rule_name', 'Rule name'), num('min_threshold', 'Min threshold'), num('max_threshold', 'Max threshold'), num('ratio', 'Ratio'), num('fee_amount', 'Fee amount'), selectField('match_dimension', 'Match dimension', surchargeDimensionOptions), num('priority', 'Priority'), flag('active', 'Active'), { name: 'condition_json', label: 'Condition JSON', kind: 'json' }, { name: 'raw_payload', label: 'Raw payload JSON', kind: 'json' }]}
        columns={[
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Rate card', dataIndex: 'rate_card_version', width: 130, render: (value) => value || '-' },
          { title: 'Code', dataIndex: 'code', width: 100 },
          { title: 'Name', dataIndex: 'rule_name' },
          { title: 'Dimension', dataIndex: 'match_dimension', width: 120 },
          { title: 'Min', dataIndex: 'min_threshold', width: 100 },
          { title: 'Max', dataIndex: 'max_threshold', width: 100 },
          { title: 'Fee', dataIndex: 'fee_amount', width: 100 },
          { title: 'Ratio', dataIndex: 'ratio', width: 100 },
          { title: 'Active', dataIndex: 'active', width: 120, render: boolRender },
        ]}
      />
    ),
    adjustments: (
      <ResourceTable
        title="Adjustment Rules"
        endpoint="/adjustment-rules/"
        filters={[
          { name: 'active', label: 'Active', kind: 'boolean' },
          { name: 'carrier', label: 'Carrier', kind: 'select', options: carrierOptions },
          { name: 'rate_card', label: 'Rate card', kind: 'select', options: rateCardOptions },
          { name: 'platform', label: 'Platform', kind: 'select', options: platformOptions },
          { name: 'service', label: 'Service', kind: 'select', options: serviceOptions },
          { name: 'state', label: 'State' },
          { name: 'postcode', label: 'Postcode' },
          { name: 'action', label: 'Action', kind: 'select', options: adjustmentActionOptions },
        ]}
        fields={[text('name', 'Name', true), flag('active', 'Active'), num('priority', 'Priority'), selectField('carrier', 'Carrier', carrierOptions), selectField('rate_card', 'Rate card', rateCardOptions), selectField('platform', 'Platform', platformOptions), selectField('service', 'Service', serviceOptions), text('state', 'State'), text('suburb', 'Suburb'), text('postcode', 'Postcode'), text('zone_code', 'Zone code'), text('sku_pattern', 'SKU pattern'), selectField('action', 'Action', adjustmentActionOptions, true), num('amount', 'Amount'), num('percent', 'Percent'), text('valid_from', 'Valid from YYYY-MM-DD'), text('valid_to', 'Valid to YYYY-MM-DD'), flag('stop_processing', 'Stop processing'), { name: 'notes', label: 'Notes', kind: 'textarea' }]}
        columns={[
          { title: 'Name', dataIndex: 'name' },
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: carrierNameRender },
          { title: 'Platform', dataIndex: 'platform_code', width: 120, render: (value) => value || '-' },
          { title: 'Service', dataIndex: 'service_code', width: 100, render: (value) => value || '-' },
          { title: 'Action', dataIndex: 'action', width: 160 },
          { title: 'Suburb', dataIndex: 'suburb', width: 150 },
          { title: 'Postcode', dataIndex: 'postcode', width: 120 },
          { title: 'Amount', dataIndex: 'amount', width: 110 },
          { title: 'Percent', dataIndex: 'percent', width: 110 },
          { title: 'Priority', dataIndex: 'priority', width: 100 },
          { title: 'Active', dataIndex: 'active', width: 120, render: boolRender },
        ]}
      />
    ),
    users: (
      <ResourceTable
        title="Users & Roles"
        endpoint="/users/"
        fields={[text('email', 'Email', true), text('display_name', 'Display name'), text('role', 'Role', true), flag('is_active', 'Active')]}
        columns={[
          { title: 'Email', dataIndex: 'email' },
          { title: 'Name', dataIndex: 'display_name' },
          { title: 'Role', dataIndex: 'role', width: 180 },
          { title: 'Active', dataIndex: 'is_active', width: 120, render: boolRender },
        ]}
      />
    ),
    audit: (
      <ResourceTable
        title="Audit Logs"
        endpoint="/audit-logs/"
        fields={[]}
        columns={[
          { title: 'Action', dataIndex: 'action', width: 140 },
          { title: 'Entity', dataIndex: 'entity_type', width: 140 },
          { title: 'Entity ID', dataIndex: 'entity_id', width: 110 },
          { title: 'Actor', dataIndex: 'actor_email', width: 220 },
          { title: 'Created', dataIndex: 'created_at', width: 200 },
        ]}
      />
    ),
  }
  if (page === 'dashboard') return <Dashboard />
  if (page === 'manualQuote') return <ManualQuote />
  if (page === 'quoteRuns') return <QuoteRuns />
  if (page === 'rateCards') return <RateCards />
  if (page === 'skus') return <SKUMaster />
  if (page === 'quoteChannels') return <QuoteChannels />
  if (page === 'invoiceReconciliation') return <InvoiceReconciliation />
  if (page === 'freightAudit') return <FreightAuditMatrix />
  if (page === 'historical') return <HistoricalOrders />
  if (page === 'lspApiQuotes') return <LspApiQuotes />
  if (page === 'users') return <AccessManagement />
  return pages[page] || <Dashboard />
}

function App() {
  const queryClient = useQueryClient()
  const [page, setPage] = useState<MenuKey>('manualQuote')
  const [authVersion, setAuthVersion] = useState(0)
  const allowDevAuth = import.meta.env.VITE_ALLOW_DEV_AUTH === 'true' || import.meta.env.MODE === 'test'
  const hasStoredToken = Boolean(getAccessToken())
  const { data: user, isError, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: async () => (await api.get<UserProfile>('/auth/me', { timeout: ACCESS_REQUEST_TIMEOUT_MS })).data,
    enabled: hasStoredToken || allowDevAuth || authVersion > 0,
    retry: false,
  })
  const can = useCallback((permission: string) => Boolean(user?.permissions?.includes('*') || user?.permissions?.includes(permission)), [user?.permissions])
  const canAny = useCallback((permissions: string[]) => permissions.some(can), [can])
  const visibleMenuItems = useMemo(() => buildVisibleMenuItems(can, canAny), [can, canAny])

  useEffect(() => {
    if (isError) clearAccessToken()
  }, [isError])

  useEffect(() => {
    if (user && !canAny(['quote.manual']) && visibleMenuItems[0]?.key && page === 'manualQuote') {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPage(visibleMenuItems[0].key as MenuKey)
    }
  }, [canAny, page, user, visibleMenuItems])

  const handleAuthenticated = (nextUser: UserProfile) => {
    queryClient.setQueryData(['me'], nextUser)
    setAuthVersion((value) => value + 1)
  }

  const logout = () => {
    clearAccessToken()
    queryClient.clear()
    setPage('manualQuote')
    setAuthVersion((value) => value + 1)
  }

  if ((!hasStoredToken && !allowDevAuth && !user) || isError) {
    return <LoginPage onAuthenticated={handleAuthenticated} />
  }

  if (isLoading && !user) {
    return <div className="app-loading">Loading access...</div>
  }

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          borderRadius: 6,
          colorPrimary: '#1677ff',
          colorBgLayout: '#f5f7fa',
          fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Arial',
        },
        components: {
          Card: { paddingLG: 16 },
          Form: { itemMarginBottom: 12 },
          Table: {
            cellPaddingBlockSM: 6,
            cellPaddingInlineSM: 8,
            headerBg: '#f8fafc',
          },
        },
      }}
    >
      <Layout className="app-shell">
        <Sider width={248} className="app-sider">
          <div className="brand">
            <div className="brand-mark">AU</div>
            <div>
              <Typography.Text strong>Freight Estimator</Typography.Text>
              <Typography.Text type="secondary">CourieDelivery</Typography.Text>
            </div>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[page]}
            onClick={({ key }) => setPage(key as MenuKey)}
            items={visibleMenuItems}
          />
        </Sider>
        <Layout>
          <Header className="app-header">
            <Space>
              <Tag color="blue">Local</Tag>
              <Tag color="green">PostgreSQL</Tag>
            </Space>
            <Space>
              <Avatar>{(user?.display_name || user?.email || 'D').charAt(0)}</Avatar>
              <div className="user-block">
                <Typography.Text>{user?.display_name || 'Dev Admin'}</Typography.Text>
                <Typography.Text type="secondary">{user?.role || 'ADMIN'}</Typography.Text>
              </div>
              <Button size="small" icon={<LogoutOutlined />} onClick={logout}>
                Logout
              </Button>
            </Space>
          </Header>
          <Content className="app-content">
            <ResourcePage page={page} />
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default App
