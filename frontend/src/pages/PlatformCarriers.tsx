import { CheckSquareOutlined, ClearOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { api, listResource } from '../api/client'
import type { Carrier, CarrierService, Platform, PlatformCarrier } from '../types'

type ConfigureResponse = {
  links: PlatformCarrier[]
}

function carrierServices(carrier: Carrier) {
  return (carrier.services || []).filter((service) => service.active)
}

function quoteSourceFor(carrier: Carrier) {
  return carrier.carrier_type || (carrier.support_api ? 'API' : 'TABLE')
}

export function PlatformCarriers() {
  const [messageApi, contextHolder] = message.useMessage()
  const queryClient = useQueryClient()
  const [selectedPlatformId, setSelectedPlatformId] = useState<number>()
  const [selectedServiceIds, setSelectedServiceIds] = useState<number[]>([])
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')

  const platformsQuery = useQuery({ queryKey: ['/platforms/'], queryFn: () => listResource<Platform>('/platforms/?page_size=1000') })
  const allCarriersQuery = useQuery({
    queryKey: ['/carriers/', 'all-for-platform-config'],
    queryFn: () => listResource<Carrier>('/carriers/?page_size=1000'),
  })
  const carriersQuery = useQuery({
    queryKey: ['/carriers/', search],
    queryFn: () => {
      const params = new URLSearchParams({ page_size: '1000' })
      if (search.trim()) params.set('search', search.trim())
      return listResource<Carrier>(`/carriers/?${params.toString()}`)
    },
  })
  const configQuery = useQuery({
    queryKey: ['/platform-carriers/configure/', selectedPlatformId],
    enabled: Boolean(selectedPlatformId),
    queryFn: async () => {
      const { data } = await api.get<ConfigureResponse>('/platform-carriers/configure/', { params: { platform: selectedPlatformId } })
      return data
    },
  })

  const platforms = useMemo(() => platformsQuery.data || [], [platformsQuery.data])
  const allCarriers = useMemo(() => allCarriersQuery.data || [], [allCarriersQuery.data])
  const carriers = useMemo(() => carriersQuery.data || [], [carriersQuery.data])

  useEffect(() => {
    const handle = window.setTimeout(() => setSearch(searchText.trim()), 350)
    return () => window.clearTimeout(handle)
  }, [searchText])

  useEffect(() => {
    if (!selectedPlatformId && platforms.length) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedPlatformId(platforms.find((platform) => platform.active)?.id || platforms[0].id)
    }
  }, [platforms, selectedPlatformId])

  useEffect(() => {
    if (configQuery.data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedServiceIds(configQuery.data.links.filter((link) => link.enabled).map((link) => link.service))
    }
  }, [configQuery.data])

  const serviceMap = useMemo(() => {
    const map = new Map<number, CarrierService>()
    allCarriers.forEach((carrier) => {
      carrier.services?.forEach((service) => map.set(service.id, service))
    })
    return map
  }, [allCarriers])

  const carrierMap = useMemo(() => new Map(allCarriers.map((carrier) => [carrier.id, carrier])), [allCarriers])
  const selectedSet = useMemo(() => new Set(selectedServiceIds), [selectedServiceIds])
  const allServiceIds = useMemo(
    () => allCarriers.filter((carrier) => carrier.active).flatMap((carrier) => carrierServices(carrier).map((service) => service.id)),
    [allCarriers],
  )

  const selectedCarrierCount = useMemo(() => {
    const carrierIds = new Set<number>()
    selectedServiceIds.forEach((serviceId) => {
      const service = serviceMap.get(serviceId)
      if (service) carrierIds.add(service.carrier)
    })
    return carrierIds.size
  }, [selectedServiceIds, serviceMap])

  const replaceCarrierServices = (carrier: Carrier, nextServiceIds: number[]) => {
    const serviceIds = new Set(carrierServices(carrier).map((service) => service.id))
    setSelectedServiceIds((current) => [...current.filter((serviceId) => !serviceIds.has(serviceId)), ...nextServiceIds])
  }

  const toggleCarrier = (carrier: Carrier, checked: boolean) => {
    const ids = carrierServices(carrier).map((service) => service.id)
    replaceCarrierServices(carrier, checked ? ids : [])
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const selections = selectedServiceIds
        .map((serviceId) => {
          const service = serviceMap.get(serviceId)
          const carrier = service ? carrierMap.get(service.carrier) : undefined
          if (!service || !carrier) return null
          return {
            carrier: carrier.id,
            service: service.id,
            quote_source: quoteSourceFor(carrier),
            priority: 100,
          }
        })
        .filter(Boolean)
      const { data } = await api.put<ConfigureResponse & { updated: number }>('/platform-carriers/configure/', {
        platform: selectedPlatformId,
        selections,
      })
      return data
    },
    onSuccess: (data) => {
      setSelectedServiceIds(data.links.filter((link) => link.enabled).map((link) => link.service))
      queryClient.invalidateQueries({ queryKey: ['/platform-carriers/'] })
      queryClient.invalidateQueries({ queryKey: ['/platform-carriers/configure/', selectedPlatformId] })
      messageApi.success(`Platform carrier config saved: ${data.updated} active service(s)`)
    },
    onError: () => messageApi.error('Platform carrier config save failed'),
  })

  const columns: ColumnsType<Carrier> = [
    {
      title: '',
      width: 54,
      render: (_, carrier) => {
        const services = carrierServices(carrier)
        const selectedCount = services.filter((service) => selectedSet.has(service.id)).length
        return (
          <Checkbox
            checked={services.length > 0 && selectedCount === services.length}
            disabled={!carrier.active || services.length === 0}
            indeterminate={selectedCount > 0 && selectedCount < services.length}
            onChange={(event) => toggleCarrier(carrier, event.target.checked)}
          />
        )
      },
    },
    {
      title: 'Carrier',
      dataIndex: 'code',
      width: 280,
      render: (_, carrier) => (
        <div className="carrier-option-cell">
          <strong>{carrier.name || carrier.code}</strong>
          <Typography.Text type="secondary">System code: {carrier.code}</Typography.Text>
        </div>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'carrier_type',
      width: 120,
      render: (value, carrier) => (
        <Space size={6}>
          <Tag color={value === 'API' ? 'blue' : value === 'HYBRID' ? 'purple' : 'default'}>{value}</Tag>
          {!carrier.active && <Tag>Disabled</Tag>}
        </Space>
      ),
    },
    {
      title: 'Services',
      render: (_, carrier) => {
        const services = carrierServices(carrier)
        if (!services.length) return <Tag>No service</Tag>
        return (
          <Checkbox.Group
            className="service-checkbox-group"
            disabled={!carrier.active}
            value={services.filter((service) => selectedSet.has(service.id)).map((service) => service.id)}
            onChange={(values) => replaceCarrierServices(carrier, values as number[])}
          >
            <Space wrap size={[12, 8]}>
              {services.map((service) => (
                <Checkbox key={service.id} value={service.id}>
                  {service.name || service.code}
                </Checkbox>
              ))}
            </Space>
          </Checkbox.Group>
        )
      },
    },
    {
      title: 'Selected',
      width: 110,
      align: 'right',
      render: (_, carrier) => carrierServices(carrier).filter((service) => selectedSet.has(service.id)).length,
    },
  ]

  return (
    <section className="page-surface platform-carrier-config">
      {contextHolder}
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Platform-Carrier Relations</Typography.Title>
          <Typography.Text type="secondary">
            {selectedCarrierCount} carrier(s), {selectedServiceIds.length} service(s) selected
          </Typography.Text>
        </div>
        <Space wrap>
          <Button icon={<CheckSquareOutlined />} onClick={() => setSelectedServiceIds(allServiceIds)}>
            Select All
          </Button>
          <Button icon={<ClearOutlined />} onClick={() => setSelectedServiceIds([])}>
            Clear
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saveMutation.isPending}
            disabled={!selectedPlatformId}
            onClick={() => saveMutation.mutate()}
          >
            Save
          </Button>
        </Space>
      </div>
      <div className="list-search-row">
        <Input.Search
          allowClear
          className="resource-search"
          placeholder="Search carrier or service"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          onSearch={(value) => setSearch(value.trim())}
        />
      </div>

      <div className="platform-carrier-controls">
        <Select
          showSearch
          className="platform-selector"
          optionFilterProp="label"
          loading={platformsQuery.isFetching}
          value={selectedPlatformId}
          options={platforms.map((platform) => ({
            value: platform.id,
            label: `${platform.code} - ${platform.name}`,
          }))}
          onChange={(value) => setSelectedPlatformId(value)}
        />
      </div>

      <Table<Carrier>
        className="resource-table platform-carrier-table"
        rowKey="id"
        loading={allCarriersQuery.isFetching || carriersQuery.isFetching || configQuery.isFetching}
        columns={columns}
        dataSource={carriers}
        pagination={{ pageSize: 12, showSizeChanger: true }}
        scroll={{ x: 'max-content' }}
      />
    </section>
  )
}
