import type { Page } from '@playwright/test'
export const testSession = { authenticated: true, csrf_token: 'test-only-csrf', identity: { user_id: 'test-user', display_name: '测试用户', role: '平台管理员', scope: 'test-workspace' } }
export async function authenticatedSession(page: Page) {
  await page.route('**/api/v27/session', route => route.fulfill({ json: testSession }))
}
