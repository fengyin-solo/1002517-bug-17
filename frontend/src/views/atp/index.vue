<template>
  <section class="page" data-module="atp">
    <header class="page-head">
      <div>
        <h2>车载设备管理</h2>
        <p class="page-desc">维护车载ATP，围绕设备编号、所属列车、设备型号、软件版本做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openInspection">排查提交</button>
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
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="keyword" placeholder="按设备编号检索" />
      </label>
      <label class="filter-item">
        <span>所属列车</span>
        <input v-model="train" placeholder="按所属列车检索" />
      </label>
      <label class="filter-item">
        <span>车载状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">查看详情</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无车载设备数据，可先调整筛选条件</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条车载设备记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 排查批量提交 -->
    <div v-if="inspectionOpen" class="modal-mask" @click.self="closeInspection">
      <div class="modal-card">
        <h3>车载设备排查提交</h3>
        <p class="modal-hint">一次提交同一列车多台设备的自检与制动接口检查结果；缺项的设备会保留原数据并在结果中点名。</p>

        <form class="inspect-load" @submit.prevent="loadTrainDevices">
          <label class="filter-item">
            <span>所属列车</span>
            <input v-model="inspectTrain" placeholder="输入所属列车，如 G123" />
          </label>
          <button class="btn" type="submit" :disabled="inspectLoading">载入本列车设备</button>
        </form>

        <table v-if="inspectRows.length" class="data-table inspect-table">
          <thead>
            <tr>
              <th>设备编号</th>
              <th>自检状态（原）</th>
              <th>本次自检</th>
              <th>制动接口（原）</th>
              <th>本次制动接口</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in inspectRows" :key="String(row.id)">
              <td>{{ row['设备编号'] }}</td>
              <td>{{ row['自检状态'] || '—' }}</td>
              <td>
                <select v-model="row._selfCheck">
                  <option value="">未填写（保留原值）</option>
                  <option value="正常">正常</option>
                  <option value="异常">异常</option>
                </select>
              </td>
              <td>{{ row['制动接口'] || '—' }}</td>
              <td>
                <select v-model="row._brake">
                  <option value="">未填写（保留原值）</option>
                  <option value="正常">正常</option>
                  <option value="异常">异常</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="inspectLoaded" class="empty-state">该列车下没有可排查的车载设备</p>

        <div v-if="inspectResults.length" class="inspect-results">
          <h4>提交结果</h4>
          <ul>
            <li v-for="(item, idx) in inspectResults" :key="idx" :class="`result-${item.result}`">
              <strong>{{ item.设备 }}</strong>：{{ item.message }}
            </li>
          </ul>
        </div>
        <p v-if="inspectMessage" :class="inspectOk ? 'ok-text' : 'error-text'">{{ inspectMessage }}</p>

        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeInspection">关闭</button>
          <button
            class="btn primary"
            type="button"
            :disabled="inspectSubmitting || !inspectRows.some(isRowFilled)"
            @click="submitInspection"
          >
            {{ inspectSubmitting ? '提交中…' : '提交排查结果' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 单条详情：直接展示后端明细，字段口径与列表一致 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal-card">
        <h3>车载设备详情</h3>
        <dl class="detail-list">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd>{{ detail[column] || '—' }}</dd>
          </template>
        </dl>
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
type InspectRow = Row & { _selfCheck: string; _brake: string }
type InspectOutcome = { result: 'success' | 'skipped' | 'failed'; 设备: string; message: string }

const ENDPOINT = '/api/atp'
const columns = ['设备编号', '所属列车', '设备型号', '软件版本', '自检状态', '制动接口', '记录数据', '车载状态']
const actions = ['登记异常', '排查通信', '办理停用']
const statuses = ['正常', '自检异常', '通信中断', '已停用']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const train = ref('')
const statusFilter = ref('')

// 统计直接基于列表同源的「车载状态」字段，避免再出现一套独立口径。
const stats = computed(() => {
  const list = rows.value
  return [
    { label: '正常车载设备', value: list.filter((r) => r['车载状态'] === '正常').length },
    { label: '异常车载设备', value: list.filter((r) => r['车载状态'] === '自检异常').length },
    { label: '通信中断设备', value: list.filter((r) => r['车载状态'] === '通信中断').length },
  ]
})

function resetFilters() {
  keyword.value = ''
  train.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (train.value) query.set('train', train.value)
  window.open(`${ENDPOINT}/export?${query.toString()}`, '_blank')
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

// ----------------------------------------------------------------------
// 排查批量提交
// ----------------------------------------------------------------------
const inspectionOpen = ref(false)
const inspectTrain = ref('')
const inspectRows = ref<InspectRow[]>([])
const inspectLoaded = ref(false)
const inspectLoading = ref(false)
const inspectSubmitting = ref(false)
const inspectResults = ref<InspectOutcome[]>([])
const inspectMessage = ref('')
const inspectOk = ref(true)
// 每次打开面板生成新票据；同一票据重复提交，后端只回放首次结论。
let submissionToken = ''

function openInspection() {
  inspectionOpen.value = true
  inspectTrain.value = train.value
  inspectRows.value = []
  inspectResults.value = []
  inspectMessage.value = ''
  inspectLoaded.value = false
  submissionToken = crypto.randomUUID()
  if (inspectTrain.value) {
    void loadTrainDevices()
  }
}

function closeInspection() {
  inspectionOpen.value = false
  void reload()
}

function isRowFilled(row: InspectRow) {
  return Boolean(row._selfCheck || row._brake)
}

async function loadTrainDevices() {
  inspectLoading.value = true
  inspectMessage.value = ''
  inspectResults.value = []
  try {
    if (!inspectTrain.value.trim()) {
      throw new Error('请先输入所属列车')
    }
    const query = new URLSearchParams({ train: inspectTrain.value.trim(), size: '200' })
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('该列车的车载设备读取失败')
    }
    const payload = await response.json()
    inspectRows.value = (payload.items ?? []).map((item: Row) => ({
      ...item,
      _selfCheck: '',
      _brake: '',
    }))
    inspectLoaded.value = true
  } catch (error) {
    inspectMessage.value = error instanceof Error ? error.message : '车载设备读取失败'
    inspectOk.value = false
  } finally {
    inspectLoading.value = false
  }
}

async function submitInspection() {
  const picked = inspectRows.value.filter(isRowFilled)
  if (!picked.length || inspectSubmitting.value) {
    return
  }
  inspectSubmitting.value = true
  inspectMessage.value = ''
  try {
    // 只提交本次真正填写了检查结果的设备，没填写的设备不参与本次提交。
    const items = picked.map((row) => ({
      设备id: row.id,
      设备编号: row['设备编号'],
      所属列车: inspectTrain.value.trim(),
      自检状态: row._selfCheck || undefined,
      制动接口: row._brake || undefined,
    }))
    const response = await request(`${ENDPOINT}/inspections`, {
      method: 'POST',
      body: JSON.stringify({ items, submission_id: submissionToken }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      throw new Error(payload?.detail ? String(payload.detail) : '排查提交失败，请稍后重试')
    }
    inspectResults.value = payload.items ?? []
    inspectMessage.value = payload.message
    inspectOk.value = payload.ok
    // 用后端返回的最新记录刷新本列车行，保证面板与列表同一套结论。
    for (const outcome of payload.items ?? []) {
      if (outcome.result !== 'success' || !outcome.entry) {
        continue
      }
      const target = inspectRows.value.find((row) => row.id === outcome.entry.id)
      if (target) {
        Object.assign(target, outcome.entry, {
          _selfCheck: '',
          _brake: '',
        })
      }
    }
    await reload()
  } catch (error) {
    inspectMessage.value = error instanceof Error ? error.message : '排查提交失败'
    inspectOk.value = false
  } finally {
    inspectSubmitting.value = false
  }
}

// ----------------------------------------------------------------------
// 详情
// ----------------------------------------------------------------------
const detail = ref<Row | null>(null)

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('车载设备详情读取失败')
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车载设备详情读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({ size: '200' })
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (train.value.trim()) query.set('train', train.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('车载ATP列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
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

.modal-card {
  background: #fff;
  border-radius: 10px;
  padding: 20px 24px;
  width: min(860px, 92vw);
  max-height: 86vh;
  overflow: auto;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.25);
}

.modal-hint {
  color: #64748b;
  font-size: 13px;
  margin: 6px 0 14px;
}

.inspect-load {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 14px;
}

.inspect-table select {
  width: 100%;
  padding: 4px 6px;
}

.inspect-results {
  margin-top: 14px;
  border-top: 1px solid #e2e8f0;
  padding-top: 10px;
}

.inspect-results ul {
  margin: 8px 0 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
}

.result-success {
  color: #15803d;
}

.result-skipped {
  color: #b45309;
}

.result-failed {
  color: #b91c1c;
}

.ok-text {
  color: #15803d;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

.detail-list {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 8px 16px;
  margin: 12px 0 0;
  font-size: 14px;
}

.detail-list dt {
  color: #64748b;
}

.detail-list dd {
  margin: 0;
}
</style>
