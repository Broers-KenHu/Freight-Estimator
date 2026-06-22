import { expect, test } from '@playwright/test'
import { setupFreightMockApi } from './support/freightMockApi'

test.describe('P2 operational configuration workflows', () => {
  test('rate card list supports server-side fuzzy search and shows current solved rate templates', async ({ page }) => {
    const api = await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Pricing').click()
    await page.getByText('Rate Cards').click()
    await expect(page.getByRole('heading', { name: 'Rate Cards' })).toBeVisible()
    await expect(page.getByText('SP-ALLIED-GRO-MEL-2023')).toBeVisible()
    await expect(page.getByText('DFE-EX-MEL-FEB-2025')).toBeVisible()

    await page.getByPlaceholder('Search this table').fill('Hunter')
    await expect(page.getByText('Hunter SYD Broers 20240920')).toBeVisible()
    await expect.poll(() => api.searches.some((entry) => entry.path === '/rate-cards/' && entry.search === 'Hunter')).toBe(true)
  })

  test('carrier master list displays carrier names and keeps normalized DFE name visible', async ({ page }) => {
    const api = await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Master Data').click()
    await page.getByText('Carriers', { exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Carriers' })).toBeVisible()
    await expect(page.getByText('Allied Express')).toBeVisible()
    await expect(page.getByText('Hunter Road Freight')).toBeVisible()
    await expect(page.getByText('Direct Freight Express')).toBeVisible()
    await expect(page.getByText('Direct Freight Parcel')).toHaveCount(0)

    await page.getByPlaceholder('Search this table').fill('direct')
    await expect(page.getByText('Direct Freight Express')).toBeVisible()
    await expect.poll(() => api.searches.some((entry) => entry.path === '/carriers/' && entry.search === 'direct')).toBe(true)
  })

  test('rate card create drawer exposes version, effective dates, approval metadata inputs, and JSON metadata', async ({ page }) => {
    await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Pricing').click()
    await page.getByText('Rate Cards').click()
    await page.getByRole('button', { name: /New/i }).click()

    const drawer = page.locator('.ant-drawer').filter({ hasText: 'New Rate Cards' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByLabel('Carrier')).toBeVisible()
    await expect(drawer.getByLabel('Name')).toBeVisible()
    await expect(drawer.locator('#version')).toBeVisible()
    await expect(drawer.getByLabel('Effective from YYYY-MM-DD')).toBeVisible()
    await expect(drawer.getByLabel('Effective to YYYY-MM-DD')).toBeVisible()
    await expect(drawer.getByLabel('GST rate')).toBeVisible()
    await expect(drawer.getByLabel('Metadata JSON')).toBeVisible()
  })
})
