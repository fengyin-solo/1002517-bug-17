<template>
  <section class="page" data-module="atp">
    <header class="page-head">
      <div>
        <h2>车载设备管理</h2>
        <p class="page-desc">维护车载ATP，围绕设备编号、所属列车、设备型号、软件版本做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selected.length" @click="openInspection">
          排查提交{{ selected.length ? `（已选 ${selected.length} 台）` : '' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出车载设备清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>车载状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th style="width: 36px">
            <input
              type="checkbox"
              :checked="allSelected"
              :disabled="!rows.length"
              @change="toggleAll"
            />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td><input type="checkbox" :value="row.id" v-model="selectedIds" /></td>
          <td v-for="column in columns" :key="column">
            <button v-if="column === '设备编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <template v-else>
              <span :class="{ 'status-tag': column === '车载状态' }">{{ row[column] ?? '—' }}</span>
            </template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无车载设备数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条车载设备记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 批量排查提交 -->
    <div v-if="inspectionOpen" class="modal-mask" @click.self="closeInspection">
      <div class="modal">
        <h3>车载设备排查提交</h3>
        <p class="page-desc">逐台填写自检状态与制动接口检查结果；未填写的检查项将保留该设备原有数据。</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>设备编号</th>
              <th>所属列车</th>
              <th>自检状态（当前：{{ selfCheckOptions.join(' / ') }}）</th>
              <th>制动接口（当前：{{ brakeOptions.join(' / ') }}）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(form, idx) in inspectionForms" :key="form.id">
              <td>{{ form.设备编号 }}</td>
              <td>{{ form.所属列车 }}</td>
              <td>
                <select v-model="form.自检状态">
                  <option value="">不修改（原值：{{ form._原自检状态 || '—' }}）</option>
                  <option v-for="s in selfCheckOptions" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
              <td>
                <select v-model="form.制动接口">
                  <option value="">不修改（原值：{{ form._原制动接口 || '—' }}）</option>
                  <option v-for="s in brakeOptions" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="submitResults.length" class="result-list">
          <p
            v-for="(r, idx) in submitResults"
            :key="`${r.id ?? r.设备编号}-${idx}`"
            :class="r.skipped ? 'result-skip' : r.ok ? 'result-ok' : 'result-fail'"
          >
            {{ r.skipped ? '⏭' : r.ok ? '✓' : '✕' }} {{ r.设备编号 || r.id }}：{{ r.message }}
          </p>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeInspection">关闭</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitInspection">
            {{ submitting ? '提交中…' : '确认提交' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 单条详情：与列表同一接口口径对照 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <h3>车载ATP明细 · {{ detail.设备编号 }}</h3>
        <table class="data-table">
          <tbody>
            <tr v-for="column in columns" :key="column">
              <th style="width: 110px">{{ column }}</th>
              <td>{{ detail[column] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail = null">知道了</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type InspectionForm = {
  id: number
  设备编号: string
  所属列车: string
  自检状态: string
  制动接口: string
  _原自检状态: string
  _原制动接口: string
}
type DeviceResult = {
  id: number | null
  设备编号: string | null
  ok: boolean
  skipped: boolean
  message: string
}

const ENDPOINT = '/api/atp'
const columns = ["设备编号", "所属列车", "设备型号", "软件版本", "自检状态", "制动接口", "记录数据", "车载状态"]
const actions = ["登记异常", "排查通信", "办理停用"]
const statuses = ["正常", "自检异常", "通信中断", "已停用"]
const selfCheckOptions = ["正常", "自检异常"]
const brakeOptions = ["正常", "制动异常"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const filterFields = columns.slice(0, 3)

const selectedIds = ref<number[]>([])
const selected = computed(() => rows.value.filter((row) => selectedIds.value.includes(Number(row.id))))
const allSelected = computed(() => rows.value.length > 0 && selectedIds.value.length === rows.value.length)

const stats = computed(() => [
  { label: "正常车载设备", value: rows.value.filter((r) => r["车载状态"] === "正常").length },
  { label: "异常车载设备", value: rows.value.filter((r) => r["车载状态"] === "自检异常").length },
  { label: "通信中断设备", value: rows.value.filter((r) => r["车载状态"] === "通信中断").length },
])

const inspectionOpen = ref(false)
const submitting = ref(false)
const inspectionForms = ref<InspectionForm[]>([])
const submitResults = ref<DeviceResult[]>([])
const detail = ref<Row | null>(null)

function toggleAll(event: Event) {
  selectedIds.value = (event.target as HTMLInputElement).checked
    ? rows.value.map((row) => Number(row.id))
    : []
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openInspection() {
  inspectionForms.value = selected.value.map((row) => ({
    id: Number(row.id),
    设备编号: String(row["设备编号"] ?? ''),
    所属列车: String(row["所属列车"] ?? ''),
    自检状态: '',
    制动接口: '',
    _原自检状态: String(row["自检状态"] ?? ''),
    _原制动接口: String(row["制动接口"] ?? ''),
  }))
  submitResults.value = []
  inspectionOpen.value = true
}

function closeInspection() {
  inspectionOpen.value = false
  submitResults.value = []
  selectedIds.value = []
}

async function submitInspection() {
  errorMessage.value = ''
  submitting.value = true
  try {
    const items = inspectionForms.value.map((form) => ({
      id: form.id,
      设备编号: form.设备编号,
      自检状态: form.自检状态 || null,
      制动接口: form.制动接口 || null,
    }))
    const response = await request(`${ENDPOINT}/inspection`, {
      method: 'POST',
      body: JSON.stringify({ items }),
    })
    if (!response.ok) {
      throw new Error('排查提交未被接收，请稍后重试')
    }
    const payload = await response.json()
    submitResults.value = payload.results ?? []
    await reload()
    // 刷新后把最新值同步进表单，避免展示旧结论
    for (const form of inspectionForms.value) {
      const latest = rows.value.find((row) => Number(row.id) === form.id)
      if (latest) {
        form._原自检状态 = String(latest["自检状态"] ?? '')
        form._原制动接口 = String(latest["制动接口"] ?? '')
      }
    }
    errorMessage.value = payload.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排查提交失败'
  } finally {
    submitting.value = false
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('车载ATP明细读取失败')
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车载ATP明细读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('车载设备动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车载设备操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams(filters.value as Record<string, string>)
  if (statusFilter.value) {
    params.set('status', statusFilter.value)
  }
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('车载ATP列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    selectedIds.value = selectedIds.value.filter((id) =>
      rows.value.some((row) => Number(row.id) === id),
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车载设备列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: min(860px, 92vw);
  max-height: 84vh;
  overflow: auto;
}
.modal h3 { margin: 0 0 8px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.result-list { margin-top: 12px; font-size: 13px; }
.result-list p { margin: 4px 0; }
.result-ok { color: #067647; }
.result-fail { color: #b42318; }
.result-skip { color: #b54708; }
.status-tag { white-space: nowrap; }
select { padding: 4px 6px; }
</style>
