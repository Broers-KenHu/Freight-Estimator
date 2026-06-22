import { describe, expect, it } from 'vitest'
import { resourceDetailUrl, unpackList } from './client'

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
