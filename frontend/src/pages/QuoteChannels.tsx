import { ApiOutlined, CheckCircleOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { api, listResource } from '../api/client'
import type { QuoteChannel } from '../types'

const samplePayload = {
  platform_code: 'SHOPIFY_AU',
  warehouse_code: 'MEL_WH',
  destination: { state: 'VIC', suburb: 'SOUTH MELBOURNE', postcode: '3205' },
  items: [{ sku: 'DEMO-CHAIR', qty: 1, unit_weight_kg: 12, length_cm: 80, width_cm: 60, height_cm: 45 }],
}

export function QuoteChannels() {
  const queryClient = useQueryClient()
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const { data = [], isFetching } = useQuery({
    queryKey: ['quote-channels', search],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '200' })
      if (search) params.set('search', search)
      return listResource<QuoteChannel>(`/quote-channels/?${params.toString()}`)
    },
  })
  const action = useMutation({
    mutationFn: async ({ id, actionName }: { id: number; actionName: string }) => (await api.post(`/quote-channels/${id}/${actionName}/`, samplePayload)).data,
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['quote-channels'] })
      if (variables.actionName === 'test') message.success(`${data.availability}: ${data.total_inc_gst || data.reason}`)
    },
  })

  useEffect(() => {
    const handle = window.setTimeout(() => setSearch(searchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [searchText])

  return (
    <section className="page-surface">
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Quote Channels</Typography.Title>
          <Typography.Text type="secondary">Enable or disable each calculator/API plugin without touching QuoteEngine code.</Typography.Text>
        </div>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          placeholder="Search carrier, service, channel, calculator"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          onSearch={(value) => setSearch(value.trim())}
        />
      </div>
      <Table<QuoteChannel>
        rowKey="id"
        loading={isFetching}
        dataSource={data}
        scroll={{ x: 1100 }}
        columns={[
          { title: 'Code', dataIndex: 'code', width: 180 },
          { title: 'Agent', width: 140, render: (_, record) => record.agent_name || record.agent_code || '-' },
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: (value, record) => value || record.carrier_code || '-' },
          { title: 'Service', dataIndex: 'service_code', width: 90 },
          { title: 'Provider', dataIndex: 'provider_type', width: 100, render: (value) => <Tag icon={<ApiOutlined />}>{value}</Tag> },
          { title: 'Calculator', dataIndex: 'calculator_key', ellipsis: true },
          { title: 'Priority', dataIndex: 'priority', width: 90 },
          { title: 'Enabled', dataIndex: 'enabled', width: 110, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? 'Enabled' : 'Disabled'}</Tag> },
          {
            title: 'Actions',
            width: 220,
            render: (_, record) => (
              <Space>
                <Button size="small" icon={record.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined />} onClick={() => action.mutate({ id: record.id, actionName: record.enabled ? 'disable' : 'enable' })}>
                  {record.enabled ? 'Disable' : 'Enable'}
                </Button>
                <Button size="small" icon={<CheckCircleOutlined />} onClick={() => action.mutate({ id: record.id, actionName: 'test' })}>
                  Test
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </section>
  )
}
