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
              z-paper 依赖以下三个 API 服务，请填写您的密钥后保存。
              密钥仅存储在本地数据库中，不会上传到任何服务器。
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

        <!-- 表单 -->
        <el-form label-position="top" @submit.prevent="save">

          <!-- DeepSeek -->
          <div class="key-group">
            <div class="key-group-header">
              <span class="key-name">DeepSeek API Key</span>
              <el-tag v-if="status.DEEPSEEK_API_KEY?.configured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="danger" size="small">未配置</el-tag>
            </div>
            <p class="key-desc">
              用于论文翻译、术语提取、知识库对话。
              申请地址：<a href="https://platform.deepseek.com" target="_blank">platform.deepseek.com</a>
            </p>
            <el-input
              v-model="form.DEEPSEEK_API_KEY"
              :placeholder="status.DEEPSEEK_API_KEY?.configured ? status.DEEPSEEK_API_KEY.masked + '（留空保留原值）' : 'sk-...'"
              :type="show.DEEPSEEK ? 'text' : 'password'"
              clearable
            >
              <template #suffix>
                <el-icon style="cursor:pointer" @click="show.DEEPSEEK = !show.DEEPSEEK">
                  <View v-if="!show.DEEPSEEK" /><Hide v-else />
                </el-icon>
              </template>
            </el-input>
          </div>

          <!-- Qwen -->
          <div class="key-group">
            <div class="key-group-header">
              <span class="key-name">通义千问 API Key</span>
              <el-tag v-if="status.QWEN_API_KEY?.configured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="danger" size="small">未配置</el-tag>
            </div>
            <p class="key-desc">
              用于图片文字识别（公式、图表）和中文论文处理。
              申请地址：<a href="https://dashscope.console.aliyun.com" target="_blank">dashscope.console.aliyun.com</a>
            </p>
            <el-input
              v-model="form.QWEN_API_KEY"
              :placeholder="status.QWEN_API_KEY?.configured ? status.QWEN_API_KEY.masked + '（留空保留原值）' : 'sk-...'"
              :type="show.QWEN ? 'text' : 'password'"
              clearable
            >
              <template #suffix>
                <el-icon style="cursor:pointer" @click="show.QWEN = !show.QWEN">
                  <View v-if="!show.QWEN" /><Hide v-else />
                </el-icon>
              </template>
            </el-input>
          </div>

          <!-- MinerU -->
          <div class="key-group">
            <div class="key-group-header">
              <span class="key-name">MinerU API Key</span>
              <el-tag v-if="status.MINERU_API_KEY?.configured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="danger" size="small">未配置</el-tag>
            </div>
            <p class="key-desc">
              用于 PDF 解析，将论文 PDF 转换为结构化文本。
              申请地址：<a href="https://mineru.net" target="_blank">mineru.net</a>
            </p>
            <el-input
              v-model="form.MINERU_API_KEY"
              :placeholder="status.MINERU_API_KEY?.configured ? status.MINERU_API_KEY.masked + '（留空保留原值）' : '填写 MinerU token'"
              :type="show.MINERU ? 'text' : 'password'"
              clearable
            >
              <template #suffix>
                <el-icon style="cursor:pointer" @click="show.MINERU = !show.MINERU">
                  <View v-if="!show.MINERU" /><Hide v-else />
                </el-icon>
              </template>
            </el-input>
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
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { View, Hide } from '@element-plus/icons-vue'
import AppHeader from '@/components/AppHeader.vue'
import api from '@/api/index.js'
import { markConfigured } from '@/router/index.js'

const router = useRouter()

const status   = ref({})   // { DEEPSEEK_API_KEY: { configured, masked, label }, ... }
const saving   = ref(false)
const form     = reactive({ DEEPSEEK_API_KEY: '', QWEN_API_KEY: '', MINERU_API_KEY: '' })
const show     = reactive({ DEEPSEEK: false, QWEN: false, MINERU: false })

// 是否是首次配置（至少一个 key 未配置）
const isFirstTime = computed(() =>
  Object.values(status.value).some(v => !v.configured)
)
const missingLabels = computed(() =>
  Object.values(status.value)
    .filter(v => !v.configured)
    .map(v => v.label)
    .join('、')
)

async function loadStatus() {
  const res = await api.get('/settings/', { skipErrorHandler: true }).catch(() => ({ data: {} }))
  status.value = res.data
}

async function save() {
  // 至少填一个
  if (!form.DEEPSEEK_API_KEY && !form.QWEN_API_KEY && !form.MINERU_API_KEY) {
    ElMessage.warning('请至少填写一个 API Key')
    return
  }
  saving.value = true
  try {
    await api.put('/settings/', {
      DEEPSEEK_API_KEY: form.DEEPSEEK_API_KEY,
      QWEN_API_KEY:     form.QWEN_API_KEY,
      MINERU_API_KEY:   form.MINERU_API_KEY,
    })
    ElMessage.success('配置已保存，立即生效')
    form.DEEPSEEK_API_KEY = ''
    form.QWEN_API_KEY     = ''
    form.MINERU_API_KEY   = ''
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
  justify-content: center;
  padding: 40px 16px;
}

.settings-card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  padding: 40px 48px;
  width: 100%;
  max-width: 640px;
}

.settings-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 32px;
}
.settings-icon { font-size: 2.4rem; line-height: 1; padding-top: 4px; }
.settings-title { font-size: 1.5rem; font-weight: 700; color: #303133; margin: 0 0 6px; }
.settings-subtitle { font-size: 0.88rem; color: #909399; margin: 0; line-height: 1.6; }

.key-group { margin-bottom: 28px; }
.key-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.key-name { font-size: 0.95rem; font-weight: 600; color: #303133; }
.key-desc { font-size: 0.82rem; color: #909399; margin: 0 0 8px; line-height: 1.5; }
.key-desc a { color: #409eff; text-decoration: none; }
.key-desc a:hover { text-decoration: underline; }

.settings-actions {
  display: flex;
  gap: 12px;
  padding-top: 8px;
}
</style>
