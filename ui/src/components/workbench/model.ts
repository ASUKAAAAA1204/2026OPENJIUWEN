export type Framework = 'openJiuwen' | 'LangGraph' | 'OpenAI Agents SDK'
export interface ToolPreview {
  id: string
  title: string
  name: string
  gate: 'NONE' | 'ALLOW'
  args: Record<string, string>
  evidence: string
}
export interface RunPreview {
  source?: 'ledger'
  id: string
  title: string
  framework: Framework
  tools: ToolPreview[]
}
export function createPreview(id: string, title: string, framework: Framework): RunPreview {
  return {
    id,
    title,
    framework,
    tools: [
      {
        id: 'read',
        title: '读取项目文档',
        name: 'read_file',
        gate: 'NONE',
        args: { path: 'workspace/README.md' },
        evidence: '只读调用示例；未采集真实输出。',
      },
      {
        id: 'write',
        title: '生成文件修改',
        name: 'file_write',
        gate: 'ALLOW',
        args: { path: 'workspace/docs/proposal.md', operation: 'create' },
        evidence: '关键决策示例；未执行写入，无真实结果证据。',
      },
    ],
  }
}
