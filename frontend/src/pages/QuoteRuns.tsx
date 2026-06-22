import { EyeOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Descriptions, Drawer, Input, Space, Table, Tabs, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { listResource } from '../api/client'
import type { QuoteCandidate, QuoteRun } from '../types'
import { nonZeroChargeLines } from '../utils/charges'

const money = (value?: string | null) => `$${Number(value || 0).toFixed(2)}`

export function QuoteRuns() {
  const [selectedRun, setSelectedRun] = useState<QuoteRun | null>(null)
  const [selectedCandidate, setSelectedCandidate] = useState<QuoteCandidate | null>(null)
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const { data = [], isFetching } = useQuery({
    queryKey: ['quote-runs', search],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '200' })
      if (search) params.set('search', search)
      return listResource<QuoteRun>(`/quote-runs/?${params.toString()}`)
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
          <Typography.Title level={2}>Quote Runs</Typography.Title>
          <Typography.Text type="secondary">Open any historical or manual quote to inspect results, breakdown and trace.</Typography.Text>
        </div>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          placeholder="Search quote source, hash, platform, warehouse, error"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          onSearch={(value) => setSearch(value.trim())}
        />
      </div>
      <Table<QuoteRun>
        rowKey="id"
        loading={isFetching}
        dataSource={data}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 80 },
          { title: 'Type', dataIndex: 'run_type', width: 130 },
          { title: 'Status', dataIndex: 'status', width: 130, render: (value) => <Tag color={value === 'COMPLETED' ? 'green' : 'red'}>{value}</Tag> },
          { title: 'Candidates', render: (_, record) => record.candidates?.length || 0, width: 110 },
          { title: 'Created', dataIndex: 'created_at', width: 210 },
          {
            title: '',
            width: 100,
            render: (_, record) => (
              <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedRun(record)}>
                Open
              </Button>
            ),
          },
        ]}
      />
      <Drawer size="large" open={Boolean(selectedRun)} onClose={() => setSelectedRun(null)} title={selectedRun ? `QuoteRun #${selectedRun.id}` : ''}>
        {selectedRun && (
          <Space direction="vertical" size="large" className="full-width">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="Run type">{selectedRun.run_type}</Descriptions.Item>
              <Descriptions.Item label="Status">{selectedRun.status}</Descriptions.Item>
              <Descriptions.Item label="Input hash" span={2}>
                {selectedRun.input_hash}
              </Descriptions.Item>
            </Descriptions>
            <Table<QuoteCandidate>
              rowKey="id"
              size="small"
              dataSource={selectedRun.candidates}
              pagination={false}
              columns={[
                { title: '#', dataIndex: 'rank', width: 56 },
                { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: (value, record) => value || record.carrier_code || '-' },
                { title: 'Channel', dataIndex: 'channel_code' },
                { title: 'Status', dataIndex: 'availability', width: 130, render: (value) => <Tag color={value === 'AVAILABLE' ? 'green' : 'red'}>{value}</Tag> },
                { title: 'Total inc GST', dataIndex: 'total_inc_gst', width: 140, align: 'right', render: money },
                {
                  title: '',
                  width: 150,
                  render: (_, record) => (
                    <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedCandidate(record)}>
                      Trace
                    </Button>
                  ),
                },
              ]}
            />
            <Tabs
              items={[
                {
                  key: 'run-trace',
                  label: 'Run Trace',
                  children: (
                    <Space direction="vertical" className="full-width">
                      {(selectedRun.trace_logs || []).map((trace) => (
                        <Card key={trace.id} size="small" title={`${trace.event_type} - ${trace.step}`}>
                          <Typography.Paragraph>{trace.message}</Typography.Paragraph>
                          <pre className="debug-json">{JSON.stringify(trace.details_json, null, 2)}</pre>
                        </Card>
                      ))}
                    </Space>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
      <Drawer size="large" open={Boolean(selectedCandidate)} onClose={() => setSelectedCandidate(null)} title={selectedCandidate?.provider_name}>
        {selectedCandidate && (
          <Tabs
            items={[
              {
                key: 'breakdown',
                label: 'Breakdown',
                children: (
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={nonZeroChargeLines(selectedCandidate.charge_lines)}
                    pagination={false}
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
                  <Space direction="vertical" className="full-width">
                    {(selectedCandidate.trace_logs || []).map((trace) => (
                      <Card key={trace.id} size="small" title={`${trace.event_type} - ${trace.step}`}>
                        <Typography.Paragraph>{trace.message}</Typography.Paragraph>
                        <pre className="debug-json">{JSON.stringify(trace.details_json, null, 2)}</pre>
                      </Card>
                    ))}
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </section>
  )
}
