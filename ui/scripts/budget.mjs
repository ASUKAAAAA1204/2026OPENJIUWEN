/**
 * 体积预算（开发计划书 §7）：build 后统计 gzip 体积，超预算即失败。
 * 用法：npm run budget
 */
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const UI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ASSETS = path.join(UI_ROOT, 'dist', 'assets')

const BUDGETS = [
  { label: '入口 JS', ext: '.js', limit: 260 * 1024 },
  { label: '入口 CSS', ext: '.css', limit: 40 * 1024 },
  { label: '字体 woff2', ext: '.woff2', limit: 600 * 1024 },
]

const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KB`

const files = await readdir(ASSETS)
const over = []
const rows = []

for (const budget of BUDGETS) {
  const hits = files.filter((name) => name.endsWith(budget.ext))
  let gzip = 0
  for (const name of hits) {
    gzip += gzipSync(await readFile(path.join(ASSETS, name))).length
  }
  const passed = gzip <= budget.limit && hits.length > 0
  rows.push(
    `${budget.label.padEnd(10)} ${kb(gzip).padStart(9)} / ${kb(budget.limit).padStart(9)}  ${
      passed ? 'PASS' : 'FAIL'
    }  (${hits.length} 个文件)`,
  )
  if (!passed) over.push(budget.label)
}

console.log(rows.join('\n'))

if (over.length > 0) {
  console.error(`体积超预算：${over.join('、')}`)
  process.exit(1)
}
