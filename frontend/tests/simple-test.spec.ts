import { test, expect } from '@playwright/test'

test('simple test', async ({ page }) => {
  expect(1 + 1).toBe(2)
})
