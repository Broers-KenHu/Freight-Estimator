import { ApiOutlined, ClockCircleOutlined, DollarOutlined, StopOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import { api } from '../api/client'

type DashboardSummary = {
  quote_runs: number
  completed_runs: number
  available_candidates: number
  not_available_candidates: number
  cheapest_total_inc_gst: string | null
  by_reason: { not_available_reason: string; count: number }[]
  system_health: {
    active_platforms: number
    active_warehouses: number
    active_carriers: number
    enabled_quote_channels: number
    ready_quote_channels: number
    active_rate_cards: number
    rate_cards_without_rules: number
    rate_cards_without_zones: number
    warehouse_platform_links: number
    platform_carrier_links: number
    warehouse_carrier_links: number
    channel_gaps: { channel: string; carrier: string; service: string; issues: string[] }[]
  }
}

export function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: async () => (await api.get<DashboardSummary>('/dashboard/summary')).data,
  })
  const health = data?.system_health
  const gapCount = health?.channel_gaps.length || 0

  return (
    <section className="page-surface">
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Dashboard</Typography.Title>
          <Typography.Text type="secondary">Live quote volume, availability and lowest quoted freight.</Typography.Text>
        </div>
      </div>
      <Row gutter={[12, 12]} className="metric-grid">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading} size="small">
            <Statistic prefix={<ClockCircleOutlined />} title="Quote runs" value={data?.quote_runs || 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading} size="small">
            <Statistic prefix={<ApiOutlined />} title="Available candidates" value={data?.available_candidates || 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading} size="small">
            <Statistic prefix={<StopOutlined />} title="Not available" value={data?.not_available_candidates || 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading} size="small">
            <Statistic title="Cheapest inc GST" value={data?.cheapest_total_inc_gst || 0} prefix="$" suffix={<DollarOutlined />} precision={2} />
          </Card>
        </Col>
      </Row>
      <Card
        size="small"
        title="System Logic Readiness"
        className="section-block"
        extra={<Tag color={gapCount ? 'gold' : 'green'}>{gapCount ? `${gapCount} gap(s)` : 'Ready'}</Tag>}
      >
        <Space direction="vertical" className="full-width" size="middle">
          <div className="health-strip">
            <div>
              <Typography.Text type="secondary">Channels</Typography.Text>
              <Typography.Text strong>
                {health?.ready_quote_channels || 0}/{health?.enabled_quote_channels || 0}
              </Typography.Text>
            </div>
            <div>
              <Typography.Text type="secondary">Rate cards</Typography.Text>
              <Typography.Text strong>{health?.active_rate_cards || 0}</Typography.Text>
            </div>
            <div>
              <Typography.Text type="secondary">Platform links</Typography.Text>
              <Typography.Text strong>{health?.platform_carrier_links || 0}</Typography.Text>
            </div>
            <div>
              <Typography.Text type="secondary">Warehouse links</Typography.Text>
              <Typography.Text strong>{health?.warehouse_carrier_links || 0}</Typography.Text>
            </div>
          </div>
          {gapCount ? (
            <Alert
              showIcon
              type="warning"
              message="Some enabled quote channels are not selectable until their platform and warehouse links are configured."
            />
          ) : (
            <Alert showIcon type="success" message="Enabled quote channels have active rate cards and required platform/warehouse links." />
          )}
          <Table
            size="small"
            rowKey="channel"
            dataSource={health?.channel_gaps || []}
            columns={[
              { title: 'Channel', dataIndex: 'channel', width: 220 },
              { title: 'Carrier', dataIndex: 'carrier', width: 140 },
              { title: 'Service', dataIndex: 'service', width: 160 },
              {
                title: 'Issues',
                dataIndex: 'issues',
                render: (issues: string[]) => <Space wrap>{issues.map((issue) => <Tag key={issue}>{issue}</Tag>)}</Space>,
              },
            ]}
            pagination={false}
          />
        </Space>
      </Card>
      <Card title="Not available reasons" className="section-block" size="small">
        <Table
          size="small"
          rowKey="not_available_reason"
          dataSource={data?.by_reason || []}
          columns={[
            { title: 'Reason', dataIndex: 'not_available_reason' },
            { title: 'Count', dataIndex: 'count', width: 120 },
          ]}
          pagination={false}
        />
      </Card>
    </section>
  )
}
