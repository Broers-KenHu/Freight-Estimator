import { Drawer, Empty, Table, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Platform, PlatformCarrier } from '../types'

type WarehouseLink = {
  id: number
  warehouse_code: string
  warehouse_name: string
  enabled: boolean
  is_default: boolean
  priority: number
}

type PlatformSummary = {
  platform: Platform
  warehouse_links: WarehouseLink[]
  carrier_links: PlatformCarrier[]
  order_summary: {
    total: number
    by_type: { source_order_type: string; count: number }[]
    with_system_estimate: number
  }
  quote_summary: {
    available_candidates: number
    lowest_total_inc_gst: string | null
    average_total_inc_gst: string | null
  }
  active_rate_cards: {
    id: number
    carrier__name: string
    service__name: string | null
    version: string
    name: string
    effective_from: string | null
    effective_to: string | null
  }[]
}

export function PlatformDetailDrawer({ platformId, open, onClose }: { platformId?: number; open: boolean; onClose: () => void }) {
  const { data, isFetching } = useQuery({
    queryKey: ['/platforms/detail-summary', platformId],
    enabled: open && Boolean(platformId),
    queryFn: async () => (await api.get<PlatformSummary>(`/platforms/${platformId}/detail-summary/`)).data,
  })

  return (
    <Drawer title="Platform Detail" width={860} open={open} onClose={onClose} destroyOnHidden loading={isFetching}>
      {data ? (
        <div className="platform-detail">
          <div className="detail-header">
            <div>
              <Typography.Title level={3}>{data.platform.name}</Typography.Title>
              <Typography.Text type="secondary">{data.platform.company || data.platform.code}</Typography.Text>
            </div>
            <div className="detail-stat-strip">
              <div>
                <Typography.Text type="secondary">Orders</Typography.Text>
                <Typography.Text strong>{Number(data.order_summary.total || 0).toLocaleString()}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">Quoted</Typography.Text>
                <Typography.Text strong>{Number(data.order_summary.with_system_estimate || 0).toLocaleString()}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">Lowest</Typography.Text>
                <Typography.Text strong>{data.quote_summary.lowest_total_inc_gst ? `$${data.quote_summary.lowest_total_inc_gst}` : '-'}</Typography.Text>
              </div>
            </div>
          </div>

          <section className="detail-section">
            <Typography.Title level={4}>Profile</Typography.Title>
            <div className="detail-kv-grid">
              <div><span>Role</span><strong>{data.platform.platform_role}</strong></div>
              <div><span>Internal type</span><strong>{data.platform.platform_type}</strong></div>
              <div><span>ERP type</span><strong>{data.platform.source_platform_type_name_en || data.platform.source_platform_type_code || '-'}</strong></div>
              <div><span>Group</span><strong>{data.platform.platform_group_name_en || data.platform.platform_group_code || '-'}</strong></div>
              <div><span>Status</span><strong>{data.platform.active ? 'Active' : 'Disabled'}</strong></div>
              <div><span>Code</span><strong>{data.platform.code}</strong></div>
            </div>
          </section>

          <section className="detail-section">
            <Typography.Title level={4}>Enabled Carrier Services</Typography.Title>
            <Table<PlatformCarrier>
              rowKey="id"
              size="small"
              dataSource={data.carrier_links}
              pagination={false}
              columns={[
                { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: (value, record) => value || record.carrier_code },
                { title: 'Service', dataIndex: 'service_name', width: 180, render: (value, record) => value || record.service_code },
                { title: 'Source', dataIndex: 'quote_source', width: 100 },
                { title: 'Enabled', dataIndex: 'enabled', width: 100, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? 'Yes' : 'No'}</Tag> },
              ]}
            />
          </section>

          <section className="detail-section">
            <Typography.Title level={4}>Warehouses</Typography.Title>
            <Table<WarehouseLink>
              rowKey="id"
              size="small"
              dataSource={data.warehouse_links}
              pagination={false}
              columns={[
                { title: 'Warehouse', dataIndex: 'warehouse_code', width: 130 },
                { title: 'Name', dataIndex: 'warehouse_name' },
                { title: 'Default', dataIndex: 'is_default', width: 100, render: (value) => <Tag color={value ? 'blue' : 'default'}>{value ? 'Yes' : 'No'}</Tag> },
                { title: 'Enabled', dataIndex: 'enabled', width: 100, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? 'Yes' : 'No'}</Tag> },
              ]}
            />
          </section>

          <section className="detail-section">
            <Typography.Title level={4}>Order Types</Typography.Title>
            {data.order_summary.by_type.length ? (
              <div className="pill-row">
                {data.order_summary.by_type.map((row) => (
                  <Tag key={row.source_order_type || 'unknown'}>{row.source_order_type || 'UNKNOWN'}: {Number(row.count || 0).toLocaleString()}</Tag>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No imported orders" />
            )}
          </section>
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </Drawer>
  )
}
