import { EditOutlined, KeyOutlined, PlusOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Drawer, Form, Input, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { api, createResource, listResource, updateResource } from '../api/client'
import type { PermissionCatalogGroup, RoleCatalogItem, UserProfile } from '../types'

const authProviderOptions = [
  { value: 'LOCAL', label: 'Local account' },
  { value: 'ENTRA', label: 'Microsoft Entra' },
  { value: 'HYBRID', label: 'Local + Microsoft Entra' },
]

type UserFormValues = Partial<UserProfile> & { password?: string }

const providerColor: Record<string, string> = {
  LOCAL: 'blue',
  ENTRA: 'purple',
  HYBRID: 'green',
}

export function AccessManagement() {
  const queryClient = useQueryClient()
  const [messageApi, contextHolder] = message.useMessage()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<UserProfile | null>(null)
  const [form] = Form.useForm<UserFormValues>()
  const { data: users = [], isFetching } = useQuery({
    queryKey: ['/users/'],
    queryFn: () => listResource<UserProfile>('/users/?page_size=500'),
  })
  const { data: permissionCatalog = [] } = useQuery({
    queryKey: ['/auth/permission-catalog'],
    queryFn: async () => (await api.get<PermissionCatalogGroup[]>('/auth/permission-catalog')).data,
  })
  const { data: roleCatalog = [] } = useQuery({
    queryKey: ['/auth/role-catalog'],
    queryFn: async () => (await api.get<RoleCatalogItem[]>('/auth/role-catalog')).data,
  })
  const roleOptions = roleCatalog.map((role) => ({ value: role.code, label: role.label }))

  const saveMutation = useMutation({
    mutationFn: async (values: UserFormValues) => {
      const payload = { ...values }
      if (!payload.password) delete payload.password
      if (editingUser) return updateResource<UserProfile>('/users/', editingUser.id, payload)
      return createResource<UserProfile>('/users/', payload)
    },
    onSuccess: () => {
      messageApi.success('User access saved')
      setDrawerOpen(false)
      queryClient.invalidateQueries({ queryKey: ['/users/'] })
    },
    onError: () => messageApi.error('User access could not be saved'),
  })

  const allPermissionCodes = useMemo(
    () => permissionCatalog.flatMap((group) => group.permissions.map((permission) => permission.code)),
    [permissionCatalog],
  )

  const openCreate = () => {
    setEditingUser(null)
    form.resetFields()
    form.setFieldsValue({ role: 'READ_ONLY', auth_provider: 'LOCAL', is_active: true, permission_overrides: [] })
    setDrawerOpen(true)
  }

  const openEdit = (user: UserProfile) => {
    setEditingUser(user)
    form.setFieldsValue({ ...user, password: '' })
    setDrawerOpen(true)
  }

  const columns: ColumnsType<UserProfile> = [
    {
      title: 'User',
      dataIndex: 'email',
      width: 260,
      render: (value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.display_name || value}</Typography.Text>
          <Typography.Text type="secondary">{value}</Typography.Text>
        </Space>
      ),
    },
    { title: 'Role', dataIndex: 'role', width: 170, render: (value) => <Tag>{value}</Tag> },
    {
      title: 'Sign-in',
      dataIndex: 'auth_provider',
      width: 170,
      render: (value, record) => (
        <Space direction="vertical" size={2}>
          <Tag color={providerColor[value] || 'default'}>{value}</Tag>
          <Typography.Text type="secondary">{record.has_local_password ? 'Local password enabled' : 'No local password'}</Typography.Text>
        </Space>
      ),
    },
    { title: 'Entra UPN', dataIndex: 'entra_upn', width: 240, render: (value) => value || '-' },
    { title: 'Permissions', dataIndex: 'permissions', width: 130, align: 'right', render: (value: string[]) => (value?.includes('*') ? 'All' : value?.length || 0) },
    { title: 'Active', dataIndex: 'is_active', width: 110, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? 'Active' : 'Disabled'}</Tag> },
    { title: 'Last login', dataIndex: 'last_login_at', width: 190, render: (value) => value || '-' },
    {
      title: '',
      width: 72,
      render: (_, record) => (
        <Button size="small" icon={<EditOutlined />} aria-label="Edit user" onClick={() => openEdit(record)} />
      ),
    },
  ]

  return (
    <section className="page-surface access-page">
      {contextHolder}
      <div className="page-toolbar">
        <div>
          <Typography.Title level={2}>Users & Access</Typography.Title>
          <Typography.Text type="secondary">Create local users, link Microsoft Entra identities, and assign operational permissions.</Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          New user
        </Button>
      </div>

      <Tabs
        items={[
          {
            key: 'users',
            label: 'Users',
            children: (
              <Table<UserProfile>
                rowKey="id"
                size="small"
                className="resource-table"
                loading={isFetching}
                dataSource={users}
                columns={columns}
                scroll={{ x: 1280 }}
              />
            ),
          },
          {
            key: 'roles',
            label: 'Role templates',
            children: (
              <div className="role-template-grid">
                {roleCatalog.map((role) => (
                  <section key={role.code}>
                    <Space align="center">
                      <SafetyCertificateOutlined />
                      <Typography.Text strong>{role.label}</Typography.Text>
                    </Space>
                    <Typography.Paragraph type="secondary">{role.description}</Typography.Paragraph>
                    <div className="pill-row">
                      {(role.resolved_permissions.includes('*') ? ['*'] : role.resolved_permissions).map((permission) => (
                        <Tag key={permission} color={permission === '*' ? 'gold' : 'blue'}>
                          {permission === '*' ? 'All permissions' : permission}
                        </Tag>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ),
          },
        ]}
      />

      <Drawer
        width={720}
        title={editingUser ? `Edit ${editingUser.email}` : 'Create user access'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={
          <Button type="primary" loading={saveMutation.isPending} onClick={() => form.submit()}>
            Save
          </Button>
        }
      >
        <Form<UserFormValues> form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          <div className="resource-drawer-form">
            <Form.Item name="email" label="Email" rules={[{ required: true }, { type: 'email' }]}>
              <Input prefix={<UserOutlined />} />
            </Form.Item>
            <Form.Item name="display_name" label="Display name">
              <Input />
            </Form.Item>
            <Form.Item name="role" label="Role" rules={[{ required: true }]}>
              <Select options={roleOptions} />
            </Form.Item>
            <Form.Item name="auth_provider" label="Sign-in type" rules={[{ required: true }]}>
              <Select options={authProviderOptions} />
            </Form.Item>
            <Form.Item name="is_active" label="Status" valuePropName="checked">
              <Checkbox>Active</Checkbox>
            </Form.Item>
            <Form.Item name="require_password_change" label="Password policy" valuePropName="checked">
              <Checkbox>Require password change</Checkbox>
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(prev, next) => prev.auth_provider !== next.auth_provider}>
              {({ getFieldValue }) => {
                const provider = getFieldValue('auth_provider')
                const needsLocal = provider === 'LOCAL' || provider === 'HYBRID'
                const needsEntra = provider === 'ENTRA' || provider === 'HYBRID'
                return (
                  <>
                    {needsLocal && (
                      <Form.Item
                        name="password"
                        label={editingUser ? 'New password' : 'Password'}
                        className="form-item-full"
                        rules={editingUser ? [] : [{ required: true }]}
                      >
                        <Input.Password prefix={<KeyOutlined />} placeholder={editingUser ? 'Leave blank to keep current password' : ''} />
                      </Form.Item>
                    )}
                    {needsEntra && (
                      <>
                        <Form.Item name="entra_oid" label="Entra object ID" className="form-item-full" rules={[{ required: true }]}>
                          <Input placeholder="Microsoft Entra user object id" />
                        </Form.Item>
                        <Form.Item name="entra_upn" label="Entra UPN">
                          <Input placeholder="name@company.com" />
                        </Form.Item>
                        <Form.Item name="entra_tid" label="Tenant ID">
                          <Input />
                        </Form.Item>
                      </>
                    )}
                  </>
                )
              }}
            </Form.Item>
            <Form.Item name="permission_overrides" label="Extra permissions" className="form-item-full">
              <Checkbox.Group className="permission-checkbox-grid">
                {permissionCatalog.map((group) => (
                  <section key={group.group}>
                    <Typography.Text strong>{group.group}</Typography.Text>
                    {group.permissions.map((permission) => (
                      <Checkbox key={permission.code} value={permission.code}>
                        <span>{permission.label}</span>
                        <em>{permission.code}</em>
                      </Checkbox>
                    ))}
                  </section>
                ))}
              </Checkbox.Group>
            </Form.Item>
            <Form.Item className="form-item-full">
              <Space>
                <Button size="small" onClick={() => form.setFieldValue('permission_overrides', allPermissionCodes)}>
                  Select all extras
                </Button>
                <Button size="small" onClick={() => form.setFieldValue('permission_overrides', [])}>
                  Clear extras
                </Button>
              </Space>
            </Form.Item>
          </div>
        </Form>
      </Drawer>
    </section>
  )
}
