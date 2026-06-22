import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./api/client', async () => {
  const actual = await vi.importActual<typeof import('./api/client')>('./api/client')
  return {
    ...actual,
    api: {
      get: vi.fn(async (url: string) => {
        if (url === '/auth/me') {
          return { data: { id: 1, email: 'dev.admin@example.com', display_name: 'Dev Admin', role: 'ADMIN', permissions: ['*'] } }
        }
        return { data: { results: [] } }
      }),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      interceptors: { request: { use: vi.fn() } },
    },
  }
})

describe('App', () => {
  it('renders the freight estimator shell', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <App />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('Freight Estimator')).toBeInTheDocument()
    expect(screen.getAllByText('Manual Quote').length).toBeGreaterThan(0)
  })
})
