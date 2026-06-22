import { CheckCircleOutlined, CloseCircleOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Tag, Tooltip, Upload, message, type UploadProps } from 'antd'
import { api, listResource } from '../api/client'
import { ResourceTable, type FieldConfig } from '../components/ResourceTable'
import type { Carrier, CarrierService, RateCard, Warehouse } from '../types'

const statusOptions = [
  { label: 'Draft', value: 'DRAFT' },
  { label: 'Active', value: 'ACTIVE' },
  { label: 'Closed', value: 'CLOSED' },
  { label: 'Archived', value: 'ARCHIVED' },
]

const taxModeOptions = [
  { label: 'Amounts exclude GST', value: 'EX_GST' },
  { label: 'Amounts include GST', value: 'INC_GST' },
  { label: 'Legacy', value: 'LEGACY' },
]

function compactCount(value: unknown) {
  const count = Number(value || 0)
  if (count >= 10000) return `${Math.round(count / 1000)}k`
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`
  return count.toLocaleString()
}

export function RateCards() {
  const [messageApi, contextHolder] = message.useMessage()
  const queryClient = useQueryClient()
  const { data: carriers = [] } = useQuery({ queryKey: ['/carriers/'], queryFn: () => listResource<Carrier>('/carriers/') })
  const { data: services = [] } = useQuery({ queryKey: ['/carrier-services/'], queryFn: () => listResource<CarrierService>('/carrier-services/') })
  const { data: warehouses = [] } = useQuery({ queryKey: ['/warehouses/'], queryFn: () => listResource<Warehouse>('/warehouses/') })

  const action = useMutation({
    mutationFn: async ({ id, name }: { id: number; name: string }) => (await api.post(`/rate-cards/${id}/${name}/`)).data,
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['/rate-cards/'] })
      messageApi.success(`Rate card ${variables.name} completed`)
    },
    onError: () => messageApi.error('Rate card action failed'),
  })

  const uploadProps = (id: number): UploadProps => ({
    name: 'file',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      const form = new FormData()
      form.append('file', file as File)
      try {
        await api.post(`/rate-cards/${id}/upload/`, form)
        queryClient.invalidateQueries({ queryKey: ['/rate-cards/'] })
        queryClient.invalidateQueries({ queryKey: ['/rate-zones/'] })
        queryClient.invalidateQueries({ queryKey: ['/rate-rules/'] })
        queryClient.invalidateQueries({ queryKey: ['/surcharge-rules/'] })
        messageApi.success('Rate card CSV uploaded')
        onSuccess?.('ok')
      } catch (error) {
        messageApi.error('Rate card CSV upload failed')
        onError?.(error as Error)
      }
    },
  })

  const fields: FieldConfig[] = [
    { name: 'carrier', label: 'Carrier', kind: 'select', required: true, options: carriers.map((carrier) => ({ value: carrier.id, label: carrier.name || carrier.code })) },
    { name: 'service', label: 'Service', kind: 'select', allowClear: true, options: services.map((service) => ({ value: service.id, label: `${service.carrier_name || service.carrier_code} / ${service.name || service.code}` })) },
    { name: 'origin_warehouse', label: 'Origin warehouse', kind: 'select', allowClear: true, options: warehouses.map((warehouse) => ({ value: warehouse.id, label: `${warehouse.code} - ${warehouse.name}` })) },
    { name: 'name', label: 'Name', required: true },
    { name: 'version', label: 'Version', required: true },
    { name: 'version_label', label: 'Version label' },
    { name: 'status', label: 'Status', kind: 'select', required: true, options: statusOptions },
    { name: 'effective_from', label: 'Effective from YYYY-MM-DD' },
    { name: 'effective_to', label: 'Effective to YYYY-MM-DD' },
    { name: 'is_active', label: 'Active', kind: 'boolean' },
    { name: 'priority', label: 'Priority', kind: 'number' },
    { name: 'currency', label: 'Currency' },
    { name: 'tax_mode', label: 'Tax mode', kind: 'select', options: taxModeOptions },
    { name: 'gst_rate', label: 'GST rate', kind: 'number' },
    { name: 'cubic_factor', label: 'Cubic factor', kind: 'number' },
    { name: 'legacy_source_object', label: 'Legacy source object' },
    { name: 'metadata_json', label: 'Metadata JSON', kind: 'json' },
  ]

  return (
    <>
      {contextHolder}
      <ResourceTable<RateCard>
        title="Rate Cards"
        endpoint="/rate-cards/"
        fields={fields}
        filters={[
          { name: 'carrier', label: 'Carrier', kind: 'select', options: carriers.map((carrier) => ({ value: carrier.id, label: carrier.name || carrier.code })) },
          { name: 'service', label: 'Service', kind: 'select', options: services.map((service) => ({ value: service.id, label: `${service.carrier_name || service.carrier_code} / ${service.name || service.code}` })) },
          { name: 'status', label: 'Status', kind: 'select', options: statusOptions },
          { name: 'tax_mode', label: 'Tax mode', kind: 'select', options: taxModeOptions },
          { name: 'is_active', label: 'Active', kind: 'boolean' },
        ]}
        actionWidth={168}
        columns={[
          { title: 'Carrier', dataIndex: 'carrier_name', width: 180, render: (value, record) => value || record.carrier_code || '-' },
          { title: 'Service', dataIndex: 'service_code', width: 110, render: (value) => value || '-' },
          { title: 'Version', dataIndex: 'version', width: 140 },
          { title: 'Status', dataIndex: 'status', width: 120, render: (value) => <Tag color={value === 'ACTIVE' ? 'green' : value === 'DRAFT' ? 'blue' : 'default'}>{value}</Tag> },
          {
            title: 'Effective',
            dataIndex: 'effective_status',
            width: 120,
            render: (value) => <Tag color={value === 'Active' ? 'green' : value === 'Expired' ? 'default' : value === 'Scheduled' ? 'blue' : 'gold'}>{value}</Tag>,
          },
          {
            title: 'Coverage',
            width: 150,
            render: (_, record) => (
              <Tooltip
                title={`Rules ${Number(record.rule_count || 0).toLocaleString()}, Zones ${Number(record.zone_count || 0).toLocaleString()}, Fees ${Number(record.surcharge_count || 0).toLocaleString()}, Channels ${Number(record.quote_channel_count || 0).toLocaleString()}`}
              >
                <span className="rate-card-coverage">
                  R{compactCount(record.rule_count)} Z{compactCount(record.zone_count)} F{compactCount(record.surcharge_count)} C{compactCount(record.quote_channel_count)}
                </span>
              </Tooltip>
            ),
          },
          { title: 'From', dataIndex: 'effective_from', width: 110, render: (value) => value || '-' },
          { title: 'To', dataIndex: 'effective_to', width: 110, render: (value) => value || '-' },
          { title: 'Active', dataIndex: 'active_now', width: 90, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? 'Yes' : 'No'}</Tag> },
          { title: 'Name', dataIndex: 'name', width: 260, ellipsis: true },
          { title: 'Warehouse', dataIndex: 'origin_warehouse_code', width: 110, render: (value) => value || '-' },
          { title: 'Priority', dataIndex: 'priority', width: 90 },
          { title: 'Uploaded by', dataIndex: 'uploaded_by_email', width: 180, render: (value) => value || '-' },
          { title: 'Approved by', dataIndex: 'approved_by_email', width: 180, render: (value) => value || '-' },
          { title: 'Approved at', dataIndex: 'approved_at', width: 200, render: (value) => value || '-' },
        ]}
        rowActions={(record) => (
          <>
            <Upload {...uploadProps(record.id)}>
              <Tooltip title="Upload CSV">
                <Button aria-label="Upload CSV" size="small" icon={<UploadOutlined />} />
              </Tooltip>
            </Upload>
            <Tooltip title="Activate">
              <Button aria-label="Activate" size="small" icon={<CheckCircleOutlined />} onClick={() => action.mutate({ id: record.id, name: 'activate' })} />
            </Tooltip>
            <Tooltip title="Close">
              <Button aria-label="Close" size="small" icon={<CloseCircleOutlined />} onClick={() => action.mutate({ id: record.id, name: 'close' })} />
            </Tooltip>
          </>
        )}
      />
    </>
  )
}
