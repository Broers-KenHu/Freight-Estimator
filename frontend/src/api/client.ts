import axios from 'axios'
import { getAccessToken } from '../auth/session'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010/api',
})

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

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
