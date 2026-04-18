import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/index.js'

const WS_BASE = import.meta.env.VITE_WS_BASE || `ws://${location.host}`
const ACTIVE_STATUSES = new Set(['pending', 'parsing', 'polishing', 'translating', 'image_translating'])
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'waiting_term_review'])

export const useJobStore = defineStore('job', () => {
  const jobs = ref([])
  const pollingTimer = ref(null)
  // job_id → WebSocket instance
  const _wsMap = {}

  async function fetchJobs() {
    const res = await api.get('/jobs')
    jobs.value = res.data
    _syncWebSockets()
  }

  // 对当前活跃的任务建立 WS 连接；已完成/失败的关掉
  function _syncWebSockets() {
    const activeIds = new Set(
      jobs.value.filter(j => ACTIVE_STATUSES.has(j.status)).map(j => j.id)
    )

    // 断开不再活跃的连接
    for (const id of Object.keys(_wsMap)) {
      if (!activeIds.has(id)) {
        _wsMap[id]?.close()
        delete _wsMap[id]
      }
    }

    // 为新活跃任务建连接
    for (const id of activeIds) {
      if (!_wsMap[id]) {
        _connectWS(id)
      }
    }
  }

  function _connectWS(jobId) {
    const url = `${WS_BASE}/ws/jobs/${jobId}`
    const ws = new WebSocket(url)

    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data)
        const idx = jobs.value.findIndex(j => j.id === jobId)
        if (idx === -1) return

        jobs.value[idx] = {
          ...jobs.value[idx],
          status: data.stage,
          progress: data.progress,
          current_stage: data.message,
          ...(data.result_id ? { result_id: data.result_id } : {}),
        }

        if (TERMINAL_STATUSES.has(data.stage)) {
          ws.close()
          delete _wsMap[jobId]
          fetchJobs()
        }
      } catch {}
    }

    ws.onerror = () => {
      delete _wsMap[jobId]
    }

    ws.onclose = () => {
      // 若非主动关闭，尝试重连一次（5 秒后）
      if (_wsMap[jobId] === ws) {
        delete _wsMap[jobId]
        setTimeout(() => {
          const job = jobs.value.find(j => j.id === jobId)
          if (job && ACTIVE_STATUSES.has(job.status)) {
            _connectWS(jobId)
          }
        }, 5000)
      }
    }

    _wsMap[jobId] = ws
  }

  function startPolling(intervalMs = 10000) {
    stopPolling()
    fetchJobs()
    pollingTimer.value = setInterval(fetchJobs, intervalMs)
  }

  function stopPolling() {
    if (pollingTimer.value) {
      clearInterval(pollingTimer.value)
      pollingTimer.value = null
    }
    // 关闭所有 WS 连接
    for (const ws of Object.values(_wsMap)) {
      ws?.close()
    }
    for (const id of Object.keys(_wsMap)) {
      delete _wsMap[id]
    }
  }

  return { jobs, fetchJobs, startPolling, stopPolling }
})
