import { createRouter, createWebHistory } from 'vue-router'
import api from '@/api/index.js'

const routes = [
  {
    path: '/settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: 'API 配置', skipSetupGuard: true }
  },
  {
    path: '/',
    component: () => import('@/views/Home.vue'),
    meta: { title: '我的论文库' }
  },
  {
    path: '/search',
    component: () => import('@/views/PaperSearch.vue'),
    meta: { title: '搜索论文' }
  },
  {
    path: '/translate',
    component: () => import('@/views/TranslateUpload.vue'),
    meta: { title: '上传翻译' }
  },
  {
    path: '/batch-upload',
    component: () => import('@/views/BatchUpload.vue'),
    meta: { title: '批量上传' }
  },
  {
    path: '/upload-chinese',
    component: () => import('@/views/ChineseUpload.vue'),
    meta: { title: '上传中文论文' }
  },
  {
    path: '/jobs',
    component: () => import('@/views/JobList.vue'),
    meta: { title: '翻译任务' }
  },
  {
    path: '/glossary',
    component: () => import('@/views/UserGlossary.vue'),
    meta: { title: '专有名词库' }
  },
  {
    path: '/chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '知识库对话' }
  },
  {
    path: '/results/by-job/:jobId',
    component: () => import('@/views/ResultReader.vue'),
    meta: { title: '阅读译文' }
  },
  {
    path: '/results/by-paper/:paperId',
    component: () => import('@/views/ResultReader.vue'),
    meta: { title: '阅读译文' }
  },
  {
    path: '/results/:resultId',
    component: () => import('@/views/ResultReader.vue'),
    meta: { title: '阅读译文' }
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || 'z-paper'} — z-paper`
})

// 配置守卫：三个 API Key 全部配置后才放行，否则一直重定向到设置页
// _configured = null 表示未检查，true 表示已配置（缓存，避免每次导航都请求）
let _configured = null
router.beforeEach(async (to) => {
  if (to.meta.skipSetupGuard) return true
  if (_configured === true) return true
  try {
    const res = await api.get('/settings/status', { skipErrorHandler: true })
    if (res.data.configured) {
      _configured = true   // 全部配置完成，后续导航无需再检查
      return true
    } else {
      _configured = null   // 保持未配置状态，每次导航都重新检查
      return { path: '/settings' }
    }
  } catch {
    return true  // 后端未就绪时放行，避免死循环
  }
})

// 供 SettingsView 在保存成功后调用，立即解除守卫拦截
export function markConfigured() {
  _configured = true
}

export default router
