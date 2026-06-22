import { expect, test } from '@playwright/test'
import { setupFreightMockApi } from './support/freightMockApi'

test.describe('P1 critical freight workflows', () => {
  test('manual quote sorts available prices, highlights the cheapest result, and exposes breakdown and trace', async ({ page }) => {
    const api = await setupFreightMockApi(page)
    await page.goto('/')

    await expect(page.getByRole('heading', { name: 'Manual Quote' })).toBeVisible()
    await expect(page.getByText('All platforms')).toBeVisible()
    await expect(page.getByText('All warehouses')).toBeVisible()

    await page.getByRole('button', { name: /Query All Rates/i }).click()
    await expect(page.getByText('QuoteRun #9001')).toBeVisible()

    const resultRows = page.locator('.quote-results .ant-table-tbody tr')
    await expect(resultRows).toHaveCount(3)
    await expect(resultRows.nth(0)).toContainText('Allied Express')
    await expect(resultRows.nth(0)).toContainText('$101.60')
    await expect(resultRows.nth(0)).toHaveClass(/quote-best-price-row/)
    await expect(resultRows.nth(1)).toContainText('Hunter Road Freight')
    await expect(resultRows.nth(2)).toContainText('Direct Freight Express')
    await expect(resultRows.nth(2)).toContainText('NOT_AVAILABLE')
    await expect(page.locator('.quote-results')).not.toContainText('Base freight')

    await resultRows.nth(0).getByRole('button', { name: /View Breakdown/i }).click()
    const drawer = page.locator('.ant-drawer').filter({ hasText: 'Allied Express' })
    await expect(drawer).toContainText('Final price')
    await expect(drawer).toContainText('$101.60')
    await expect(drawer).toContainText('Base freight')
    await expect(drawer).toContainText('Fuel levy 18.5%')
    await expect(drawer).toContainText('Residential surcharge')
    await expect(drawer).toContainText('Oversize surcharge')
    await expect(drawer).not.toContainText('OIS not triggered')

    await page.getByRole('tab', { name: 'Trace' }).click()
    await expect(drawer).toContainText('Warehouse BG01 matched MEL origin')
    await expect(drawer).toContainText('Matched destination zone')

    expect(api.lastQuotePayload?.platform_code).toBe('ALL')
    expect(api.lastQuotePayload?.warehouse_code).toBe('ALL')
    expect(api.lastQuotePayload?.destination).toMatchObject({ state: 'VIC', suburb: 'SOUTH MELBOURNE', postcode: '3205' })
  })

  test('ERP order quote lookup brings order, tracking, LSP quote, ERP carrier and SKU snapshot into manual quote', async ({ page }) => {
    await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('ERP / Platform Order').click()
    const orderInput = page.locator('.order-lookup-panel input').first()
    await orderInput.fill('O196542')
    await expect(page.locator('.ant-select-dropdown')).toContainText('O1965424994346143745')
    await page.locator('.ant-select-item-option').filter({ hasText: 'O1965424994346143745' }).first().click()

    const orderSummary = page.locator('.order-lookup-summary')
    await expect(orderSummary).toContainText('ERP Order No')
    await expect(orderSummary).toContainText('O1965424994346143745')
    await expect(orderSummary).toContainText('Platform Order No')
    await expect(orderSummary).toContainText('DP000832714')
    await expect(orderSummary).toContainText('8566090069989')
    await expect(orderSummary).toContainText('ERP Carrier')
    await expect(orderSummary).toContainText('Allied Express')
    await expect(orderSummary).toContainText('ERP Est inc GST')
    await expect(orderSummary).toContainText('$47.91')
    await expect(page.getByRole('cell', { name: 'MAT-A18-M907-Q', exact: true }).first()).toBeVisible()
    await expect(page.getByText('QuoteRun #9001')).toBeVisible()
  })

  test('invoice reconciliation review compares ERP estimate inc GST, system estimate, actual invoice and exports Excel', async ({ page }) => {
    const api = await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Invoice Reconciliation').click()
    await expect(page.getByRole('heading', { name: 'Invoice Reconciliation' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'Allied Express / BROGRO' }).first()).toBeVisible()

    await page.getByRole('button', { name: /Review/i }).click()
    const drawer = page.locator('.reconciliation-review-drawer')
    await expect(drawer).toContainText('Loaded 1 / 1')
    await expect(drawer).toContainText('O1965424994346143745')
    await expect(drawer).toContainText('8566090069989')
    await expect(drawer).toContainText('$64.02')
    await expect(drawer).toContainText('$61.19')
    await expect(drawer).toContainText('$65.70')
    await expect(drawer).toContainText('MATCHED')
    await expect(drawer).toContainText('OVERCHARGE')
    await expect(drawer).toContainText('system_quote_matched_allied_carrier')

    await drawer.getByRole('button', { name: /Export Excel/i }).click()
    await expect.poll(() => api.lastInvoiceExport).toEqual({ batchId: '244', scope: 'all' })
  })

  test('freight audit matrix shows carrier cards grouped by tracking and can trigger a build request', async ({ page }) => {
    const api = await setupFreightMockApi(page)
    await page.goto('/')

    await page.getByText('Freight Audit Matrix').click()
    await expect(page.getByRole('heading', { name: 'Freight Audit Matrix' })).toBeVisible()
    await expect(page.getByText('O1965424994346143745')).toBeVisible()
    await expect(page.getByText('$64.02')).toBeVisible()
    await expect(page.getByText('$65.70')).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'Hunter' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'Allied' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'Direct Freight' })).toBeVisible()

    await page.getByRole('row', { name: /O1965424994346143745/ }).first().click()
    const drawer = page.locator('.freight-audit-drawer')
    await expect(drawer).toContainText('Freight audit - O1965424994346143745')
    await expect(drawer).toContainText('Tracking')
    await expect(drawer).toContainText('8566090069989')
    await expect(drawer).toContainText('SP-HUNTER-SYD-2025')
    await expect(drawer).toContainText('Items used for calculation')
    await expect(drawer).toContainText('MAT-A18-M907-Q')

    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: /Build Orders/i }).click()
    await expect.poll(() => api.lastAuditBuildPayload).toMatchObject({
      batch_id: 221,
      source_config: 'HUNTER',
      mode: 'CONSIGNMENT',
      limit: 5000,
    })
  })
})
