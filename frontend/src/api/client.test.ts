import { message } from 'antd'
import type { InternalAxiosRequestConfig } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearAccessToken, getAccessToken, setAccessToken } from '../auth/session'
import { attachAuthHeader, getApiErrorMessage, handleApiError, resourceDetailUrl, unpackList } from './client'

beforeEach(() => {
  clearAccessToken()
})

describe('unpackList', () => {
  it('returns raw arrays unchanged', () => {
    expect(unpackList([{ id: 1 }])).toEqual([{ id: 1 }])
  })

  it('returns DRF paginated results', () => {
    expect(unpackList({ count: 1, next: null, previous: null, results: [{ id: 2 }] })).toEqual([{ id: 2 }])
  })
})

describe('resourceDetailUrl', () => {
  it('builds detail URLs for endpoints with or without a trailing slash', () => {
    expect(resourceDetailUrl('/carriers/', 12)).toBe('/carriers/12/')
    expect(resourceDetailUrl('/carriers', 12)).toBe('/carriers/12/')
  })
})

describe('attachAuthHeader', () => {
  it('attaches a bearer token when one is stored', () => {
    setAccessToken('abc123')

    const config = attachAuthHeader({ headers: {} } as InternalAxiosRequestConfig)

    expect(config.headers.Authorization).toBe('Bearer abc123')
  })
})

describe('getApiErrorMessage', () => {
  it('classifies common API failures', () => {
    expect(getApiErrorMessage({ isAxiosError: true, response: { status: 403 } })).toContain('Permission denied')
    expect(getApiErrorMessage({ isAxiosError: true, response: { status: 500 } })).toContain('Server error')
    expect(getApiErrorMessage({ isAxiosError: true, request: {} })).toContain('Backend is not reachable')
    expect(getApiErrorMessage({ isAxiosError: true, response: { status: 400, data: { detail: 'Bad postcode' } } })).toBe(
      'Bad postcode',
    )
  })
})

describe('handleApiError', () => {
  it('clears the stored token on 401 and annotates the error', async () => {
    setAccessToken('expired-token')
    const error = { name: 'AxiosError', message: 'Unauthorized', isAxiosError: true, response: { status: 401 } } as Error & {
      userMessage?: string
    }
    const messageSpy = vi.spyOn(message, 'error').mockImplementation(() => undefined as never)

    await expect(handleApiError(error)).rejects.toBe(error)

    expect(getAccessToken()).toBeNull()
    expect(error.userMessage).toContain('Session expired')
    expect(messageSpy).toHaveBeenCalledWith(expect.stringContaining('Session expired'))

    messageSpy.mockRestore()
  })
})
