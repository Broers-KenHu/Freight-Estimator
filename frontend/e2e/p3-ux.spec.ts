import { expect, test } from '@playwright/test'
import { setupFreightMockApi } from './support/freightMockApi'

test.describe('P3 usability and responsive regressions', () => {
  test('SKU autocomplete aligns category and SKU, supports combo SKU, and keeps full product name visible', async ({ page }) => {
    await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('SKU / Combo SKU').click()
    const skuInput = page.locator('.sku-link-input input').first()
    await skuInput.fill('ACH')

    const dropdown = page.locator('.ant-select-dropdown').filter({ hasText: 'Living Room Seating' }).first()
    await expect(dropdown).toBeVisible()
    await expect(dropdown).toContainText('Living Room Seating')
    await expect(dropdown).toContainText('||')
    await expect(dropdown).toContainText('ACH-A11-BGX2')

    const inputBox = await skuInput.boundingBox()
    const dropdownBox = await dropdown.boundingBox()
    expect(dropdownBox?.width || 0).toBeGreaterThanOrEqual(inputBox?.width || 0)

    await dropdown.locator('.ant-select-item-option').filter({ hasText: 'ACH-A11-BGX2' }).first().click()
    await expect(page.getByText('Oikiture 2x Armchair Velvet Accent Chair Full Product Name Visible')).toBeVisible()
  })

  for (const viewport of [
    { name: 'notebook 1366x768', width: 1366, height: 768 },
    { name: 'desktop 1920x1080', width: 1920, height: 1080 },
    { name: '4k 3840x2160', width: 3840, height: 2160 },
  ]) {
    test(`manual quote primary controls remain usable on ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await setupFreightMockApi(page)
      await page.goto('/')

      await expect(page.getByRole('heading', { name: 'Manual Quote' })).toBeVisible()
      await expect(page.getByText('Platform', { exact: true })).toBeVisible()
      await expect(page.getByText('Warehouse', { exact: true })).toBeVisible()
      await expect(page.getByText('Suburb', { exact: true })).toBeVisible()
      await expect(page.getByText('Line entry mode', { exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: /Query All Rates/i })).toBeVisible()

      const overflow = await page.evaluate(() => Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth))
      expect(overflow).toBeLessThan(48)
    })
  }

  test('freight audit detail drawer uses wide layout and keeps tracking sections readable', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Freight Audit Matrix').click()
    await page.getByRole('row', { name: /O1965424994346143745/ }).first().click()
    const drawerWrapper = page.locator('.ant-drawer-content-wrapper').first()
    await expect(drawerWrapper).toBeVisible()
    const box = await drawerWrapper.boundingBox()
    expect(box?.width || 0).toBeGreaterThan(1200)
    await expect(page.locator('.audit-tracking-block').first()).toContainText('8566090069989')
    await expect(page.locator('.audit-result-summary').first()).toContainText('SP-ALLIED-GRO-SYD-2023')
  })
})
