import { LockOutlined, MailOutlined, WindowsOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Button, Form, Input, Segmented, Space, Typography, message } from 'antd'
import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../api/client'
import { msalEnabled, signInWithMicrosoft, tryMicrosoftSso } from '../auth/msal'
import { setAccessToken } from '../auth/session'
import heroImage from '../assets/logistics-login-hero.png'
import type { UserProfile } from '../types'

const AUTH_REQUEST_TIMEOUT_MS = 8000

type LoginResponse = {
  access_token: string
  user: UserProfile
}

type LoginPageProps = {
  onAuthenticated: (user: UserProfile) => void
}

type LoginLanguage = 'zh' | 'en'

const languageStorageKey = 'couriedelivery.login_language'

const copy: Record<
  LoginLanguage,
  {
    benefits: Array<{ title: string; description: string }>
    eyebrow: string
    title: string
    subtitle: string
    microsoftButton: string
    localButton: string
    entraNotConfigured: string
    signedIn: string
    loginFailed: string
    microsoftTokenFailed: string
    microsoftFailed: string
  }
> = {
  zh: {
    benefits: [
      { title: '运费预估', description: '按仓库、SKU、目的地和快递费率生成报价。' },
      { title: '成本解释', description: '拆分基础运费、燃油、附加费和 GST。' },
      { title: '账单复核', description: '匹配 tracking 与账单，定位异常费用。' },
      { title: '费率管理', description: '管理多快递、多版本费率和历史回算。' },
    ],
    eyebrow: '登录',
    title: '进入系统',
    subtitle: '使用公司账号访问运费估算工作台。',
    microsoftButton: 'Microsoft 一键登录',
    localButton: '登录',
    entraNotConfigured: 'Microsoft Entra 尚未配置，当前请使用本地账号登录。',
    signedIn: '已登录',
    loginFailed: '邮箱或密码不正确',
    microsoftTokenFailed: 'Microsoft 登录未返回 API token',
    microsoftFailed: 'Microsoft 登录未完成',
  },
  en: {
    benefits: [
      { title: 'Freight Estimate', description: 'Quote by warehouse, SKU, destination and carrier rates.' },
      { title: 'Cost Explanation', description: 'Break down base freight, fuel, surcharges and GST.' },
      { title: 'Invoice Review', description: 'Match tracking with invoices and find billing variances.' },
      { title: 'Rate Management', description: 'Manage carrier rates, versions and historical recalculation.' },
    ],
    eyebrow: 'Sign in',
    title: 'Enter System',
    subtitle: 'Use your company account to access Freight Intelligence.',
    microsoftButton: 'Microsoft sign-in',
    localButton: 'Sign in',
    entraNotConfigured: 'Microsoft Entra is not configured yet. Please use a local account for now.',
    signedIn: 'Signed in',
    loginFailed: 'Email or password is incorrect',
    microsoftTokenFailed: 'Microsoft sign-in did not return an API token',
    microsoftFailed: 'Microsoft sign-in could not be completed',
  },
}

function getInitialLanguage(): LoginLanguage {
  try {
    return window.localStorage.getItem(languageStorageKey) === 'en' ? 'en' : 'zh'
  } catch {
    return 'zh'
  }
}

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [messageApi, contextHolder] = message.useMessage()
  const [silentAttempted, setSilentAttempted] = useState(false)
  const [silentLoading, setSilentLoading] = useState(false)
  const [language, setLanguage] = useState<LoginLanguage>(getInitialLanguage)
  const text = copy[language]
  const heroStyle = { '--login-hero-image': `url(${heroImage})` } as CSSProperties

  useEffect(() => {
    try {
      window.localStorage.setItem(languageStorageKey, language)
    } catch {
      // Ignore storage failures and keep the in-memory language selection.
    }
  }, [language])

  const localLogin = useMutation({
    mutationFn: async (values: { email: string; password: string }) => (await api.post<LoginResponse>('/auth/login', values)).data,
    onSuccess: (data) => {
      setAccessToken(data.access_token)
      onAuthenticated(data.user)
      messageApi.success(text.signedIn)
    },
    onError: () => messageApi.error(text.loginFailed),
  })

  const microsoftLogin = useMutation({
    mutationFn: signInWithMicrosoft,
    onSuccess: async (token) => {
      if (!token) {
        messageApi.error(text.microsoftTokenFailed)
        return
      }
      setAccessToken(token)
      const { data } = await api.get<UserProfile>('/auth/me', { timeout: AUTH_REQUEST_TIMEOUT_MS })
      onAuthenticated(data)
    },
    onError: () => messageApi.error(text.microsoftFailed),
  })

  useEffect(() => {
    if (!msalEnabled || silentAttempted) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSilentAttempted(true)
    setSilentLoading(true)
    tryMicrosoftSso()
      .then(async (token) => {
        if (!token) return
        setAccessToken(token)
        const { data } = await api.get<UserProfile>('/auth/me', { timeout: AUTH_REQUEST_TIMEOUT_MS })
        onAuthenticated(data)
      })
      .catch(() => {
        // Silent SSO should not block local login when Entra is unreachable.
      })
      .finally(() => setSilentLoading(false))
  }, [onAuthenticated, silentAttempted])

  return (
    <div className="login-shell" style={heroStyle}>
      {contextHolder}
      <section className="login-visual" aria-label="Freight Intelligence">
        <div className="login-brand-block">
          <Typography.Title level={1}>Freight Intelligence</Typography.Title>
        </div>

        <div className="login-benefit-cards">
          {text.benefits.map((item) => (
            <div key={item.title}>
              <b>{item.title}</b>
              <span>{item.description}</span>
            </div>
          ))}
        </div>
      </section>

      <main className="login-panel">
        <div className="login-panel-inner">
          <div className="login-panel-toolbar">
            <Segmented
              size="small"
              value={language}
              options={[
                { label: '中文', value: 'zh' },
                { label: 'EN', value: 'en' },
              ]}
              onChange={(value) => setLanguage(value as LoginLanguage)}
            />
          </div>

          <Space direction="vertical" size={4}>
            <Typography.Text className="login-eyebrow">{text.eyebrow}</Typography.Text>
            <Typography.Title level={2}>{text.title}</Typography.Title>
            <Typography.Text type="secondary">{text.subtitle}</Typography.Text>
          </Space>

          <Button
            className="login-microsoft-button"
            block
            size="large"
            icon={<WindowsOutlined />}
            loading={microsoftLogin.isPending || silentLoading}
            onClick={() => {
              if (msalEnabled) {
                microsoftLogin.mutate()
                return
              }
              messageApi.info(text.entraNotConfigured)
            }}
          >
            {text.microsoftButton}
          </Button>

          <div className="login-local-form">
            <Form layout="vertical" onFinish={(values) => localLogin.mutate(values)}>
              <Form.Item name="email" label="Email" rules={[{ required: true }, { type: 'email' }]}>
                <Input size="large" prefix={<MailOutlined />} placeholder="name@company.com" />
              </Form.Item>
              <Form.Item name="password" label="Password" rules={[{ required: true }]}>
                <Input.Password size="large" prefix={<LockOutlined />} placeholder="Password" />
              </Form.Item>
              <Button block type="primary" size="large" htmlType="submit" loading={localLogin.isPending}>
                {text.localButton}
              </Button>
            </Form>
          </div>
        </div>
      </main>
    </div>
  )
}
