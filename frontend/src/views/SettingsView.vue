<template>
  <div class="settings-layout">
    <AppHeader />
    <div class="settings-body">
      <div class="settings-card">

        <!-- 标题区 -->
        <div class="settings-header">
          <div class="settings-icon">🔑</div>
          <div>
            <h1 class="settings-title">API 配置</h1>
            <p class="settings-subtitle">
              按服务商分别配置。密钥仅存储在本地数据库中，不会上传到任何服务器。
            </p>
          </div>
        </div>

        <!-- 首次未配置提示 -->
        <el-alert
          v-if="isFirstTime"
          title="首次使用 — 请先完成配置"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 24px"
        >
          <template #default>
            检测到以下密钥尚未配置：
            <strong>{{ missingLabels }}</strong>。
            填写并保存后即可正常使用所有功能。
          </template>
        </el-alert>

        <el-form label-position="top" @submit.prevent="save">

          <!-- 服务商分组卡片 -->
          <div v-for="p in PROVIDERS" :key="p.key" class="provider-card">
            <div class="provider-header">
              <span class="provider-icon">{{ p.icon }}</span>
              <span class="provider-name">{{ p.name }}</span>
              <el-tag v-if="keys[p.keyField]?.configured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="danger" size="small">未配置</el-tag>
            </div>
            <p class="provider-desc">
              {{ p.desc }} ·
              申请地址：<a :href="p.applyUrl" target="_blank">{{ p.applyText }}</a>
            </p>

            <!-- API Key -->
            <div class="field">
              <label class="field-label">API Key</label>
              <el-input
                v-model="form[p.keyField]"
                :placeholder="keys[p.keyField]?.configured ? keys[p.keyField].masked + '（留空保留原值）' : p.keyPlaceholder"
                :type="show[p.keyField] ? 'text' : 'password'"
                clearable
              >
                <template #suffix>
                  <el-icon style="cursor:pointer" @click="show[p.keyField] = !show[p.keyField]">
                    <View v-if="!show[p.keyField]" /><Hide v-else />
                  </el-icon>
                </template>
              </el-input>
            </div>

            <!-- 模型 / 地址 -->
            <div v-for="f in p.fields" :key="f.key" class="field">
              <label class="field-label">
                {{ f.label }}
                <el-tag v-if="advanced[f.key]?.modified" type="warning" size="small" effect="plain">已自定义</el-tag>
              </label>
              <div class="field-row">
                <el-input
                  v-model="form[f.key]"
                  :placeholder="advanced[f.key]?.default"
                  clearable
                />
                <el-button
                  v-if="(form[f.key] || '') !== (advanced[f.key]?.default || '')"
                  size="small"
                  @click="resetDefault(f.key)"
                >
                  恢复默认
                </el-button>
              </div>
              <p class="field-hint">默认值：<code>{{ advanced[f.key]?.default }}</code></p>
            </div>
          </div>

          <!-- 操作按钮 -->
          <div class="settings-actions">
            <el-button
              type="primary"
              :loading="saving"
              native-type="submit"
              size="large"
            >
              保存配置
            </el-button>
            <el-button v-if="!isFirstTime" size="large" @click="$router.back()">
              返回
            </el-button>
          </div>
        </el-form>

      </div>
      <DataSyncPanel />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { View, Hide } from '@element-plus/icons-vue'
import AppHeader from '@/components/AppHeader.vue'
import DataSyncPanel from '@/components/DataSyncPanel.vue'
import api from '@/api/index.js'
import { markConfigured } from '@/router/index.js'

const router = useRouter()

const PROVIDERS = [
  {
    key: 'deepseek',
    name: 'DeepSeek',
    icon: '🐋',
    desc: '论文翻译、术语提取、知识库对话',
    applyUrl: 'https://platform.deepseek.com',
    applyText: 'platform.deepseek.com',
    keyField: 'DEEPSEEK_API_KEY',
    keyPlaceholder: 'sk-...',
    fields: [
      { key: 'DEEPSEEK_BASE_URL', label: 'API 地址' },
      { key: 'DEEPSEEK_MODEL', label: '模型名' },
    ],
  },
  {
    key: 'qwen',
    name: '阿里百炼 · 通义千问',
    icon: '☁️',
    desc: '图片文字识别（公式 / 图表）、图片生成与编辑',
    applyUrl: 'https://dashscope.console.aliyun.com',
    applyText: 'dashscope.console.aliyun.com',
    keyField: 'QWEN_API_KEY',
    keyPlaceholder: 'sk-...',
    fields: [
      { key: 'QWEN_BASE_URL', label: 'OpenAI 兼容地址' },
      { key: 'QWEN_DASHSCOPE_BASE_URL', label: 'DashScope 地址' },
      { key: 'QWEN_VL_MODEL', label: '视觉模型（图片 / 公式识别）' },
      { key: 'QWEN_IMAGE_MODEL', label: '图片编辑模型' },
      { key: 'QWEN_IMAGE_GEN_MODEL', label: '图片生成模型' },
    ],
  },
  {
    key: 'mineru',
    name: 'MinerU',
    icon: '📄',
    desc: 'PDF 解析，将论文 PDF 转换为结构化文本',
    applyUrl: 'https://mineru.net',
    applyText: 'mineru.net',
    keyField: 'MINERU_API_KEY',
    keyPlaceholder: '填写 MinerU token',
    fields: [
      { key: 'MINERU_BASE_URL', label: 'API 地址' },
    ],
  },
]

const KEY_FIELDS = PROVIDERS.map(p => p.keyField)
const ADVANCED_FIELDS = PROVIDERS.flatMap(p => p.fields.map(f => f.key))

const keys     = ref({})   // { KEY: { configured, masked, label } }
const advanced = ref({})   // { KEY: { value, default, label, modified } }
const saving   = ref(false)
const form     = reactive({
  DEEPSEEK_API_KEY: '', QWEN_API_KEY: '', MINERU_API_KEY: '',
  DEEPSEEK_BASE_URL: '', DEEPSEEK_MODEL: '', QWEN_BASE_URL: '',
  QWEN_DASHSCOPE_BASE_URL: '', QWEN_VL_MODEL: '', QWEN_IMAGE_MODEL: '',
  QWEN_IMAGE_GEN_MODEL: '', MINERU_BASE_URL: '',
})
const show = reactive({ DEEPSEEK_API_KEY: false, QWEN_API_KEY: false, MINERU_API_KEY: false })

// 是否是首次配置（至少一个 key 未配置）
const isFirstTime = computed(() =>
  Object.values(keys.value).some(v => !v.configured)
)
const missingLabels = computed(() =>
  Object.values(keys.value)
    .filter(v => !v.configured)
    .map(v => v.label)
    .join('、')
)

function fieldMeta(key) {
  for (const p of PROVIDERS) {
    const f = p.fields.find(x => x.key === key)
    if (f) return { provider: p.name, label: f.label }
  }
  return { provider: '', label: key }
}

function advancedChanges() {
  return ADVANCED_FIELDS.filter(k => {
    const cur = advanced.value[k]?.value || ''
    const val = (form[k] || '').trim()
    return val !== '' && val !== cur
  })
}

function resetDefault(key) {
  form[key] = advanced.value[key]?.default || ''
}

async function loadStatus() {
  const res = await api.get('/settings/', { skipErrorHandler: true }).catch(() => ({ data: {} }))
  keys.value     = res.data.keys || {}
  advanced.value = res.data.advanced || {}
  // 预填高级字段当前值
  for (const k of ADVANCED_FIELDS) {
    form[k] = advanced.value[k]?.value || ''
  }
}

async function save() {
  const keyFilled = KEY_FIELDS.some(k => form[k])
  const changes   = advancedChanges()

  if (!keyFilled && changes.length === 0) {
    ElMessage.warning('没有需要保存的修改')
    return
  }

  // 修改模型名 / API 地址前二次确认
  if (changes.length > 0) {
    const detail = changes
      .map(k => {
        const m = fieldMeta(k)
        return `· ${m.provider} ${m.label}：${advanced.value[k]?.value || '（空）'} → ${form[k].trim()}`
      })
      .join('\n')
    try {
      await ElMessageBox.confirm(
        `即将修改以下模型 / API 地址：\n\n${detail}\n\n修改后可能导致翻译、识别或图片功能失败，请确认无误。`,
        '确认修改模型配置',
        {
          type: 'warning',
          confirmButtonText: '确认修改',
          cancelButtonText: '取消',
          distinguishCancelAndClose: true,
        }
      )
    } catch {
      return  // 用户取消
    }
  }

  saving.value = true
  try {
    const payload = {}
    for (const k of KEY_FIELDS) {
      if (form[k]) payload[k] = form[k]
    }
    for (const k of changes) {
      payload[k] = form[k].trim()
    }
    await api.put('/settings/', payload)
    ElMessage.success('配置已保存，立即生效')

    for (const k of KEY_FIELDS) form[k] = ''
    await loadStatus()

    // 首次配置完成后跳转首页
    if (!isFirstTime.value) {
      markConfigured()   // 解除路由守卫拦截
      setTimeout(() => router.push('/'), 800)
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadStatus)
</script>

<style scoped>
.settings-layout { min-height: 100vh; display: flex; flex-direction: column; background: #f5f7fa; }

.settings-body {
  flex: 1;
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  gap: 24px;
  justify-content: center;
  padding: 40px 24px;
}

.settings-card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  padding: 40px 48px;
  flex: 1 1 0;
  min-width: 0;
  max-width: 760px;
}

/* 同 WLAN 数据同步面板（DataSyncPanel 根元素），与设置卡片左右并排 */
:deep(.sync-card) {
  flex: 1 1 0;
  min-width: 0;
  max-width: 760px;
}

@media (max-width: 960px) {
  .settings-body {
    flex-direction: column;
    align-items: center;
  }
  .settings-card,
  :deep(.sync-card) {
    width: 100%;
    max-width: 760px;
  }
}

.settings-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 28px;
}
.settings-icon { font-size: 2.4rem; line-height: 1; padding-top: 4px; }
.settings-title { font-size: 1.5rem; font-weight: 700; color: #303133; margin: 0 0 6px; }
.settings-subtitle { font-size: 0.88rem; color: #909399; margin: 0; line-height: 1.6; }

/* 服务商卡片 */
.provider-card {
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  padding: 20px 22px;
  margin-bottom: 20px;
  background: #fff;
}
.provider-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.provider-icon { font-size: 1.35rem; line-height: 1; }
.provider-name { font-size: 1.05rem; font-weight: 700; color: #303133; }
.provider-desc { font-size: 0.82rem; color: #909399; margin: 0 0 18px; line-height: 1.5; }
.provider-desc a { color: #409eff; text-decoration: none; }
.provider-desc a:hover { text-decoration: underline; }

/* 字段 */
.field { margin-bottom: 16px; }
.field:last-child { margin-bottom: 0; }
.field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}
.field-row { display: flex; gap: 8px; align-items: center; }
.field-row .el-input { flex: 1; }
.field-hint { font-size: 0.75rem; color: #c0c4cc; margin: 4px 0 0; line-height: 1.4; }
.field-hint code {
  background: #f4f4f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.72rem;
  color: #909399;
  word-break: break-all;
}

.settings-actions {
  display: flex;
  gap: 12px;
  padding-top: 4px;
}
</style>
