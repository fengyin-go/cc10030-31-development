/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
  /** 指标 2/3 对应的状态；不填时按 statuses[1] / statuses[last] 统计。 */
  metricStatuses?: [string, string]
  /** 登记表单字段；留空沿用 fields（用于只读展示列较多的模块）。 */
  createFields?: string[]
  /** 需要额外录入参数的动作：动作名 → 参数字段。留空表示直接执行。 */
  actionInputs?: Record<string, string[]>
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
