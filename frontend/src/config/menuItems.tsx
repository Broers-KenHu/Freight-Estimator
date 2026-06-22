import {
  ApiOutlined,
  ApartmentOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  CalculatorOutlined,
  CarOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  DollarOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  HomeOutlined,
  SettingOutlined,
  TagsOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'

export type MenuKey =
  | 'dashboard'
  | 'manualQuote'
  | 'quoteRuns'
  | 'platforms'
  | 'agents'
  | 'carriers'
  | 'carrierServices'
  | 'platformCarriers'
  | 'warehouses'
  | 'warehousePlatforms'
  | 'warehouseCarriers'
  | 'skus'
  | 'rateCards'
  | 'rateZones'
  | 'rateRules'
  | 'surcharges'
  | 'adjustments'
  | 'quoteChannels'
  | 'invoiceReconciliation'
  | 'freightAudit'
  | 'historical'
  | 'lspApiQuotes'
  | 'users'
  | 'audit'

type MenuItem = NonNullable<MenuProps['items']>[number]
type Can = (permission: string) => boolean
type CanAny = (permissions: string[]) => boolean

const menuItems = (items: Array<MenuItem | false | null | undefined>) => items.filter(Boolean) as MenuItem[]

export function buildVisibleMenuItems(can: Can, canAny: CanAny): MenuItem[] {
  return menuItems([
    can('dashboard.view') && { key: 'dashboard', icon: <BarChartOutlined />, label: 'Dashboard' },
    can('quote.manual') && { key: 'manualQuote', icon: <CalculatorOutlined />, label: 'Manual Quote' },
    can('quote.history.view') && { key: 'quoteRuns', icon: <HistoryOutlined />, label: 'Quote Runs' },
    canAny(['master.view', 'sku.view']) && {
      key: 'master',
      icon: <DatabaseOutlined />,
      label: 'Master Data',
      children: menuItems([
        can('master.view') && { key: 'platforms', icon: <AppstoreOutlined />, label: 'Platforms' },
        can('master.view') && { key: 'agents', icon: <ApiOutlined />, label: 'Agents' },
        can('master.view') && { key: 'carriers', icon: <CarOutlined />, label: 'Carriers' },
        can('master.view') && { key: 'carrierServices', icon: <TagsOutlined />, label: 'Carrier Services' },
        can('master.view') && { key: 'platformCarriers', icon: <ClusterOutlined />, label: 'Platform-Carriers' },
        can('master.view') && { key: 'warehouses', icon: <HomeOutlined />, label: 'Warehouses' },
        can('master.view') && { key: 'warehousePlatforms', icon: <ApartmentOutlined />, label: 'Warehouse Platforms' },
        can('master.view') && { key: 'warehouseCarriers', icon: <DeploymentUnitOutlined />, label: 'Warehouse Carriers' },
        can('sku.view') && { key: 'skus', icon: <TagsOutlined />, label: 'SKU Master' },
      ]),
    },
    can('pricing.view') && {
      key: 'pricing',
      icon: <DollarOutlined />,
      label: 'Pricing',
      children: [
        { key: 'rateCards', label: 'Rate Cards' },
        { key: 'rateZones', label: 'Rate Zones' },
        { key: 'rateRules', label: 'Rate Rules' },
        { key: 'surcharges', label: 'Surcharges' },
        { key: 'adjustments', label: 'Adjustment Rules' },
      ],
    },
    can('order.view') && { key: 'historical', icon: <HistoryOutlined />, label: 'Order Imports' },
    can('order.view') && { key: 'lspApiQuotes', icon: <ApiOutlined />, label: 'LSP API Quotes' },
    can('invoice.view') && { key: 'invoiceReconciliation', icon: <FileSearchOutlined />, label: 'Invoice Reconciliation' },
    can('quote.audit.view') && { key: 'freightAudit', icon: <CalculatorOutlined />, label: 'Freight Audit Matrix' },
    can('integration.view') && { key: 'quoteChannels', icon: <ApiOutlined />, label: 'Quote Channels' },
    canAny(['user.view', 'audit.view']) && {
      key: 'admin',
      icon: <SettingOutlined />,
      label: 'Admin',
      children: menuItems([
        can('user.view') && { key: 'users', label: 'Users & Roles' },
        can('audit.view') && { key: 'audit', icon: <AuditOutlined />, label: 'Audit Logs' },
      ]),
    },
  ])
}
