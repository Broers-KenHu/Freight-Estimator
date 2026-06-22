import axios, { type InternalAxiosRequestConfig } from 'axios'
import { message } from 'antd'
import { clearAccessToken, getAccessToken } from '../auth/session'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010/api',
})

export function attachAuthHeader(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
}

export function getApiErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return 'Unexpected error. Please try again.'
  const status = error.response?.status
  if (status === 401) return 'Session expired. Please sign in again.'
  if (status === 403) return 'Permission denied. Please contact an administrator if you need access.'
  if (status && status >= 500) return 'Server error. Please try again or contact support.'
  if (!error.response) return 'Backend is not reachable. Check that the API server is running.'
  const data = error.response.data as { detail?: unknown; message?: unknown } | undefined
  const detail = data?.detail || data?.message
  return typeof detail === 'string' && detail ? detail : 'Request failed. Please check the input and try again.'
}

export function handleApiError(error: unknown): Promise<never> {
  const userMessage = getApiErrorMessage(error)
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 401) clearAccessToken()
    const apiError = error as typeof error & { userMessage?: string }
    apiError.userMessage = userMessage
  }
  message.error(userMessage)
  return Promise.reject(error)
}

api.interceptors.request.use(attachAuthHeader)
api.interceptors.response.use(
  (response) => response,
  handleApiError,
)

export type Paginated<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export function unpackList<T>(data: Paginated<T> | T[]): T[] {
  return Array.isArray(data) ? data : data.results
}

export async function listResource<T>(endpoint: string): Promise<T[]> {
  const { data } = await api.get<Paginated<T> | T[]>(endpoint)
  return unpackList<T>(data)
}

export async function createResource<T>(endpoint: string, payload: unknown): Promise<T> {
  const { data } = await api.post<T>(endpoint, payload)
  return data
}

export function resourceDetailUrl(endpoint: string, id: number | string): string {
  return `${endpoint.replace(/\/+$/, '')}/${id}/`
}

export async function updateResource<T>(endpoint: string, id: number, payload: unknown): Promise<T> {
  const { data } = await api.patch<T>(resourceDetailUrl(endpoint, id), payload)
  return data
}

export async function deleteResource(endpoint: string, id: number): Promise<void> {
  await api.delete(resourceDetailUrl(endpoint, id))
}
