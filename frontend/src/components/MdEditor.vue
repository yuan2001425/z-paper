<template>
  <div class="md-editor">
    <div class="md-toolbar">
      <button class="md-btn" title="粗体 (Ctrl+B)" @mousedown.prevent="wrap('**', '**')"><b>B</b></button>
      <button class="md-btn" title="斜体 (Ctrl+I)" @mousedown.prevent="wrap('*', '*')"><i>I</i></button>
      <button class="md-btn md-btn-code" title="行内代码" @mousedown.prevent="wrap('`', '`')">{ }</button>
      <span class="md-hint">支持 **粗体** *斜体* `代码`</span>
    </div>
    <div class="md-body">
      <textarea
        ref="taRef"
        class="md-textarea"
        :value="modelValue"
        :rows="rows"
        placeholder="输入批注内容..."
        @input="$emit('update:modelValue', $event.target.value)"
        @keydown.ctrl.b.prevent="wrap('**', '**')"
        @keydown.ctrl.i.prevent="wrap('*', '*')"
      />
      <div class="md-divider" />
      <div class="md-preview" v-html="rendered || '<span class=\'preview-empty\'>预览</span>'" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  rows: { type: Number, default: 5 },
})
const emit = defineEmits(['update:modelValue'])
const taRef = ref(null)

const rendered = computed(() => {
  const text = props.modelValue
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code class="ann-code">$1</code>')
    .replace(/\n/g, '<br>')
})

function wrap(before, after) {
  const ta = taRef.value
  if (!ta) return
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const val = props.modelValue
  const selected = val.slice(start, end)
  const newVal = val.slice(0, start) + before + selected + after + val.slice(end)
  emit('update:modelValue', newVal)
  nextTick(() => {
    ta.focus()
    if (selected) {
      ta.setSelectionRange(start + before.length, end + before.length)
    } else {
      ta.setSelectionRange(start + before.length, start + before.length)
    }
  })
}
</script>

<style scoped>
.md-editor { border: 1px solid #dcdfe6; border-radius: 4px; overflow: hidden; }

.md-toolbar {
  display: flex; align-items: center; gap: 2px;
  padding: 4px 6px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}
.md-btn {
  padding: 2px 8px; border: 1px solid #dcdfe6; border-radius: 3px;
  background: #fff; cursor: pointer; font-size: 0.85rem; line-height: 1.6;
  color: #303133; transition: background 0.15s, border-color 0.15s;
}
.md-btn:hover { background: #ecf5ff; border-color: #409eff; color: #409eff; }
.md-btn-code { font-family: monospace; font-size: 0.8rem; }
.md-hint { margin-left: auto; font-size: 0.75rem; color: #c0c4cc; }

.md-body {
  display: flex;
  min-height: 120px;
}

.md-textarea {
  flex: 1;
  box-sizing: border-box;
  padding: 8px 10px;
  border: none;
  outline: none;
  resize: none;
  font-size: 0.9rem;
  line-height: 1.6;
  color: #303133;
  font-family: inherit;
  min-height: 120px;
}

.md-divider {
  width: 1px;
  background: #e4e7ed;
  flex-shrink: 0;
}

.md-preview {
  flex: 1;
  box-sizing: border-box;
  padding: 8px 10px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: #303133;
  background: #fafafa;
  overflow-y: auto;
  word-break: break-word;
}
</style>

<style>
.preview-empty { color: #c0c4cc; font-size: 0.85rem; }
.ann-code { background: #f5f7fa; padding: 1px 4px; border-radius: 3px; font-family: monospace; font-size: 0.85em; }
</style>
