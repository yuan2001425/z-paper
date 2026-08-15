<template>
  <section class="sync-card">
    <div class="sync-header">
      <div>
        <h2 class="sync-title">同 WLAN 数据同步</h2>
        <p class="sync-subtitle">接收端先打开检测，发送端扫描设备后发起申请；接收端同意后只传输缺失数据。</p>
      </div>
      <el-button :icon="Refresh" text :loading="refreshing" @click="refreshAll">刷新</el-button>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="同步会导入来源设备中本机缺少的数据和文件，API 配置会更新为来源设备配置；导入前会先备份本机数据库。"
      style="margin-bottom: 18px"
    />

    <div class="sync-section">
      <div class="section-head">
        <span>本机状态</span>
        <div class="tag-row">
          <el-tag size="small">v{{ device.version || '-' }}</el-tag>
          <el-tag size="small" type="info">UDP {{ device.discovery_port || '-' }}</el-tag>
          <el-tag v-if="device.discoverable" size="small" type="success">可被发现</el-tag>
        </div>
      </div>
      <div class="address-row">
        <el-select v-model="sourceBaseUrl" placeholder="未发现局域网地址" style="flex:1">
          <el-option v-for="url in sourceOptions" :key="url" :value="url" :label="url" />
        </el-select>
        <el-button :icon="CopyDocument" @click="copySourceUrl">复制本机地址</el-button>
        <el-button type="success" :loading="enabling" @click="enableDiscovery">打开检测</el-button>
      </div>
      <p class="hint">
        若扫描不到设备，请确认两台电脑在同一 WLAN，并允许防火墙放行 TCP 8000 和 UDP {{ device.discovery_port || 37621 }}。
      </p>
      <p v-if="device.discoverable_until" class="hint">检测开放至：{{ formatTime(device.discoverable_until) }}</p>
    </div>

    <div class="sync-section">
      <div class="section-head">
        <span>扫描可同步设备</span>
        <el-button type="primary" :icon="Search" :loading="scanning" @click="scanDevices">扫描设备</el-button>
      </div>

      <el-empty v-if="scannedDevices.length === 0" description="暂无扫描结果" :image-size="72" />
      <div v-for="item in scannedDevices" :key="item.base_url" class="device-row">
        <div class="request-main">
          <div class="request-name">{{ item.device_name || 'z-paper 设备' }}</div>
          <div class="request-url">{{ item.base_url }}</div>
        </div>
        <div class="request-actions">
          <el-tag :type="item.same_version ? 'success' : 'danger'" size="small">
            {{ item.version ? `v${item.version}` : '未知版本' }}
          </el-tag>
          <el-button
            size="small"
            type="primary"
            :disabled="!item.same_version || !sourceBaseUrl || isLoopbackUrl(sourceBaseUrl)"
            :loading="sending[item.base_url]"
            @click="sendRequest(item)"
          >发送申请</el-button>
        </div>
      </div>
    </div>

    <div class="sync-section">
      <div class="section-head">
        <span>待同意申请</span>
        <el-badge v-if="pendingRequests.length" :value="pendingRequests.length" />
      </div>

      <el-empty v-if="pendingRequests.length === 0" description="暂无同步申请" :image-size="72" />
      <div v-for="item in pendingRequests" :key="item.request_id" class="request-row">
        <div class="request-main">
          <div class="request-name">{{ item.source_name || '来源设备' }}</div>
          <div class="request-url">{{ item.source_base_url }}</div>
          <div class="request-expire">过期时间：{{ formatTime(item.expires_at) }}</div>
        </div>
        <div class="request-actions">
          <el-tag size="small">v{{ item.source_version || '-' }}</el-tag>
          <el-button
            type="success"
            size="small"
            :icon="Check"
            @click="acceptRequest(item)"
          >同意并导入</el-button>
          <el-button size="small" :icon="Close" @click="declineRequest(item.request_id)">拒绝</el-button>
        </div>
      </div>
    </div>

    <div class="sync-section">
      <div class="section-head">
        <span>同步日志</span>
      </div>
      <div class="activity-log">
        <div v-for="(line, index) in activityLogs" :key="index" class="log-line">{{ line }}</div>
        <div v-if="activityLogs.length === 0" class="log-empty">暂无日志</div>
      </div>
    </div>

    <el-dialog
      v-model="operationVisible"
      title="同步进度"
      width="640px"
      :close-on-click-modal="operation?.status !== 'running'"
      :show-close="operation?.status !== 'running'"
    >
      <el-progress
        :percentage="operation?.progress || 0"
        :status="operation?.status === 'failed' ? 'exception' : (operation?.status === 'completed' ? 'success' : undefined)"
      />
      <div class="operation-status">
        {{ operation?.status === 'completed' ? '同步完成' : operation?.status === 'failed' ? '同步失败' : '正在同步' }}
      </div>
      <el-alert v-if="operation?.error" type="error" :title="operation.error" :closable="false" style="margin:12px 0" />
      <div v-if="resultSummary.length" class="result-summary">
        <div v-for="(line, index) in resultSummary" :key="index">{{ line }}</div>
      </div>
      <div class="operation-log">
        <div v-for="(item, index) in operationLogs" :key="index" class="log-line">
          {{ formatTime(item.time) }} · {{ item.message }}
        </div>
      </div>
      <template #footer>
        <el-button v-if="operation?.status !== 'running'" @click="operationVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, CopyDocument, Refresh, Search } from '@element-plus/icons-vue'
import api from '@/api/index.js'

const device = ref({})
const pendingRequests = ref([])
const scannedDevices = ref([])
const sourceBaseUrl = ref('')
const refreshing = ref(false)
const enabling = ref(false)
const scanning = ref(false)
const sending = reactive({})
const activityLogs = ref([])
const operation = ref(null)
const operationVisible = ref(false)
let requestTimer = null
let operationTimer = null

function isLoopbackUrl(value) {
  try {
    const host = new URL(value).hostname.toLowerCase()
    return host === 'localhost' || host === '127.0.0.1' || host.startsWith('127.')
  } catch {
    return false
  }
}

const sourceOptions = computed(() => {
  const urls = new Set(device.value.suggested_urls || [])
  const host = window.location.hostname
  if (host && host !== 'localhost' && host !== '127.0.0.1') {
    urls.add(`${window.location.protocol}//${host}:8000`)
  }
  return Array.from(urls).filter((url) => !isLoopbackUrl(url))
})

const operationLogs = computed(() => operation.value?.logs || [])
const resultSummary = computed(() => {
  const result = operation.value?.result
  if (!result) return []
  const delta = result.delta || {}
  const lines = [
    `导入数据：${result.row_count ?? delta.row_count ?? 0} 条`,
    `导入文件：${result.upload_count ?? delta.file_count ?? 0} 个，约 ${formatBytes(result.upload_bytes ?? delta.file_size ?? 0)}`,
  ]
  if (delta.conflict_count) {
    lines.push(`跳过冲突文件：${delta.conflict_count} 个（未覆盖本机已有文件）`)
  }
  if (result.skipped_existing_files) {
    lines.push(`跳过已存在文件：${result.skipped_existing_files} 个`)
  }
  if (result.skipped_missing_files) {
    lines.push(`跳过包内缺失文件：${result.skipped_missing_files} 个`)
  }
  if (result.missing_source_count) {
    lines.push(`来源端缺失文件：${result.missing_source_count} 个（已跳过）`)
  }
  if (result.backup_path) {
    lines.push(`本机备份：${result.backup_path}`)
  }
  return lines
})

watch(sourceOptions, (options) => {
  if (!options.includes(sourceBaseUrl.value)) {
    sourceBaseUrl.value = options[0] || ''
  }
}, { immediate: true })

function log(message) {
  activityLogs.value.unshift(`${new Date().toLocaleTimeString('zh-CN')} · ${message}`)
  activityLogs.value = activityLogs.value.slice(0, 80)
}

async function loadDevice() {
  const res = await api.get('/sync/device', { skipErrorHandler: true })
  device.value = res.data || {}
}

async function loadRequests() {
  const res = await api.get('/sync/requests', { skipErrorHandler: true })
  pendingRequests.value = res.data?.requests || []
}

async function refreshAll() {
  refreshing.value = true
  try {
    await Promise.all([loadDevice(), loadRequests()])
  } finally {
    refreshing.value = false
  }
}

async function copySourceUrl() {
  if (!sourceBaseUrl.value) return
  try {
    await navigator.clipboard.writeText(sourceBaseUrl.value)
    ElMessage.success('已复制本机同步地址')
  } catch {
    ElMessage.warning('浏览器未允许自动复制，请手动选中地址复制')
  }
}

async function enableDiscovery() {
  enabling.value = true
  try {
    const res = await api.post('/sync/discovery/enable', { minutes: 10 })
    log(`已打开检测，其他设备可在 10 分钟内发现本机（UDP ${res.data.discovery_port}）`)
    await loadDevice()
  } catch (err) {
    ElMessage.error('打开检测失败：' + (err.response?.data?.detail || err.message))
  } finally {
    enabling.value = false
  }
}

async function scanDevices() {
  scanning.value = true
  scannedDevices.value = []
  try {
    log('开始扫描同 WLAN 内已打开检测的 z-paper 设备')
    const res = await api.post('/sync/discovery/scan', { seconds: 3 })
    scannedDevices.value = res.data?.devices || []
    log(`扫描完成，发现 ${scannedDevices.value.length} 台设备`)
    if (scannedDevices.value.length === 0) {
      ElMessage.warning('没有发现设备，请在接收端先点击“打开检测”')
    }
  } catch (err) {
    ElMessage.error('扫描失败：' + (err.response?.data?.detail || err.message))
  } finally {
    scanning.value = false
  }
}

async function sendRequest(item) {
  if (!sourceBaseUrl.value || isLoopbackUrl(sourceBaseUrl.value)) {
    ElMessage.error('未检测到可用于跨设备同步的局域网地址，请确认已连接 WLAN，并允许 TCP 8000 的局域网访问')
    return
  }
  sending[item.base_url] = true
  try {
    await api.post('/sync/outbound-requests', {
      target_base_url: item.base_url,
      source_base_url: sourceBaseUrl.value || undefined,
      source_name: device.value.device_name || 'z-paper',
    })
    log(`已向 ${item.device_name || item.base_url} 发送同步申请`)
    ElMessage.success('同步申请已发送，请在接收方电脑上同意')
  } catch (err) {
    ElMessage.error('发送失败：' + (err.response?.data?.detail || err.message))
  } finally {
    sending[item.base_url] = false
  }
}

async function acceptRequest(item) {
  await ElMessageBox.confirm(
    '同意后会从来源设备导入本机缺少的数据和文件，API 配置会更新为来源设备配置；本机旧数据库会先备份。确认继续？',
    '确认接收同步',
    {
      type: 'warning',
      confirmButtonText: '同意并导入',
      cancelButtonText: '取消',
    }
  )
  try {
    const res = await api.post(`/sync/requests/${item.request_id}/accept`)
    log(`已同意来自 ${item.source_name || '来源设备'} 的同步申请`)
    watchOperation(res.data.operation_id)
  } catch (err) {
    ElMessage.error('启动导入失败：' + (err.response?.data?.detail || err.message))
  }
}

async function declineRequest(requestId) {
  await api.delete(`/sync/requests/${requestId}`)
  log('已拒绝同步申请')
  await loadRequests()
}

function watchOperation(operationId) {
  operationVisible.value = true
  if (operationTimer) clearInterval(operationTimer)
  const poll = async () => {
    const res = await api.get(`/sync/operations/${operationId}`, { skipErrorHandler: true })
    operation.value = res.data
    if (operation.value.status !== 'running') {
      clearInterval(operationTimer)
      operationTimer = null
      await loadRequests()
      log(operation.value.status === 'completed' ? '同步完成' : `同步失败：${operation.value.error || ''}`)
    }
  }
  poll()
  operationTimer = setInterval(poll, 1000)
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN') : '-'
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

onMounted(async () => {
  await refreshAll()
  requestTimer = setInterval(loadRequests, 5000)
})

onUnmounted(() => {
  if (requestTimer) clearInterval(requestTimer)
  if (operationTimer) clearInterval(operationTimer)
})
</script>

<style scoped>
.sync-card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  padding: 32px 36px;
  width: 100%;
  max-width: 760px;
}
.sync-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.sync-title { font-size: 1.28rem; font-weight: 700; color: #303133; margin: 0 0 6px; }
.sync-subtitle { font-size: 0.88rem; color: #909399; margin: 0; line-height: 1.6; }
.sync-section {
  padding: 18px 0;
  border-top: 1px solid #ebeef5;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  font-weight: 600;
  color: #303133;
}
.tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.address-row {
  display: flex;
  gap: 10px;
  align-items: center;
}
.hint {
  margin: 8px 0 0;
  color: #909399;
  font-size: 0.8rem;
}
.device-row,
.request-row {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 0;
  border-top: 1px solid #f0f2f5;
}
.device-row:first-of-type,
.request-row:first-of-type { border-top: none; }
.request-main { min-width: 0; }
.request-name { font-weight: 600; color: #303133; margin-bottom: 4px; }
.request-url, .request-expire {
  color: #909399;
  font-size: 0.8rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.request-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-content: center;
  align-items: center;
}
.activity-log,
.operation-log {
  max-height: 220px;
  overflow: auto;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
  padding: 10px 12px;
}
.operation-log { margin-top: 12px; max-height: 260px; }
.result-summary {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #f4f9ff;
  color: #606266;
  font-size: 0.82rem;
  line-height: 1.7;
}
.log-line {
  color: #606266;
  font-size: 0.8rem;
  line-height: 1.7;
}
.log-empty {
  color: #c0c4cc;
  font-size: 0.8rem;
}
.operation-status {
  margin-top: 10px;
  color: #606266;
  font-size: 0.9rem;
}
@media (max-width: 720px) {
  .sync-card { padding: 24px 20px; }
  .sync-header, .address-row, .device-row, .request-row { flex-direction: column; align-items: stretch; }
  .request-actions { justify-content: flex-start; }
}
</style>
