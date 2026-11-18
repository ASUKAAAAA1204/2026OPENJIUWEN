import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:5273',
    viewport: { width: 1280, height: 720 },
    trace: 'retain-on-failure',
  },
  expect: {
    /** 视觉回归：像素差异 ≤0.1%，不屏蔽任何区域（计划书 §3.4） */
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.001,
      animations: 'disabled',
      caret: 'hide',
    },
  },
  webServer: {
    command: 'npm run dev -- --port 5273 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:5273',
    reuseExistingServer: true,
    timeout: 60000,
  },
})
