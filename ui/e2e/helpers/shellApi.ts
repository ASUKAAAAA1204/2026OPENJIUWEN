import type { Page } from '@playwright/test'
import { authenticatedSession } from './authenticatedSession'
import { runContextFixture, lifecycleFixture } from '../../tests/fixtures/lifecycle'

/** 外壳接口的测试夹具；前端接入真实后端后移除 */
export async function shellApi(page: Page) {
  await authenticatedSession(page)
  await page.route('**/api/v27/run-context', route =>
    route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs/*/lifecycle', route =>
    route.fulfill({ json: lifecycleFixture() }))
}
