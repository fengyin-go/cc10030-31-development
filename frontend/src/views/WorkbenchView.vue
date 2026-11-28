<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="openAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in createFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="actionPrompt" class="modal-backdrop" data-testid="action-modal">
      <form class="modal" @submit.prevent="submitAction">
        <h3>{{ actionPrompt.action }} · {{ actionPrompt.rowLabel }}</h3>
        <p v-if="actionPrompt.hint" class="page-desc">{{ actionPrompt.hint }}</p>
        <label v-for="field in actionPrompt.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="actionValues[field]" :data-testid="`action-input-${field}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="actionPrompt = null">取消</button>
          <button class="btn primary" data-testid="submit-action" type="submit" :disabled="acting">
            {{ acting ? '提交中…' : '提交办理' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const createFields = meta.createFields ?? meta.fields
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const acting = ref(false)
const actionValues = ref<Record<string, string>>({})
const actionPrompt = ref<{
  action: string
  rowLabel: string
  rowId: number
  fields: string[]
  hint: string
} | null>(null)
const metricStatuses = meta.metricStatuses ?? [
  meta.statuses[1] ?? '',
  meta.statuses[meta.statuses.length - 1] ?? '',
]
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === metricStatuses[0]).length },
  { label: meta.metrics[2], value: rows.value.filter((row) => row.status === metricStatuses[1]).length },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(createFields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow, extra: Record<string, string> = {}) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action, extra)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

const ACTION_HINTS: Record<string, string> = {
  签认航次: '受预警影响时系统会提示先区分：继续按原计划执行选「继续签认」，需要调整航次选「先办改派」。',
  继续签认: '确认按原计划继续签认；需要引用补正回执时可填写回执编号。',
  先办改派: '签认前提交原航次改派；没有可用凭据时按提示先在气象预警上重新核对经停。',
  调整经停: '填写补报后的经停港口，保存后会重新核对预警补正回执的受影响航次。',
  重新核对经停: '按最新航线数据重新核对受影响航次，空态也会保留待办回执。',
}

function openAction(action: string, row: EntryRow) {
  const fields = meta.actionInputs?.[action] ?? []
  actionValues.value = Object.fromEntries(fields.map((field) => [field, '']))
  if (fields.length) {
    actionPrompt.value = {
      action,
      rowLabel: `${meta.entity} #${row.id}`,
      rowId: Number(row.id),
      fields,
      hint: ACTION_HINTS[action] ?? '',
    }
    return
  }
  void runAction(action, row)
}

async function submitAction() {
  if (!actionPrompt.value) return
  acting.value = true
  const { action, rowId } = actionPrompt.value
  try {
    const values = Object.fromEntries(
      Object.entries(actionValues.value).filter(([, value]) => value.trim()),
    )
    await runAction(action, { id: rowId } as EntryRow, values)
    actionPrompt.value = null
  } finally {
    acting.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
