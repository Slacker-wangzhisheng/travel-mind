<script setup>
import { ref } from 'vue'

defineProps({
  memories: { type: Array, default: () => [] },
})
const emit = defineEmits(['confirm', 'update', 'remove', 'add'])

const TYPES = [
  { value: 'interest', label: '兴趣' },
  { value: 'companion', label: '同行人群' },
  { value: 'physical', label: '体力偏好' },
  { value: 'departure', label: '常用出发地' },
]

const newType = ref('interest')
const newContent = ref('')
const editing = ref(null)
const editingText = ref('')

function typeLabel(value) {
  return TYPES.find((t) => t.value === value)?.label || value
}

function startEdit(m) {
  editing.value = m.id
  editingText.value = m.content
}

function saveEdit(m) {
  const text = editingText.value.trim()
  if (text && text !== m.content) emit('update', m.id, { content: text })
  editing.value = null
}

function submitNew() {
  const text = newContent.value.trim()
  if (!text) return
  emit('add', newType.value, text)
  newContent.value = ''
}
</script>

<template>
  <div class="memory-panel">
    <div class="mp-title">
      我的长期偏好
      <span class="hint">仅在你确认后生效</span>
    </div>

    <p v-if="!memories.length" class="empty">暂无长期偏好</p>

    <div v-for="m in memories" :key="m.id" class="mem-item">
      <div class="mem-head">
        <span class="tag">{{ typeLabel(m.type) }}</span>
        <span class="badge" :class="m.status">{{ m.status === 'confirmed' ? '已确认' : '待确认' }}</span>
      </div>
      <div v-if="editing === m.id" class="edit-row">
        <input v-model="editingText" type="text" @keydown.enter="saveEdit(m)" />
        <button type="button" @click="saveEdit(m)">保存</button>
      </div>
      <div v-else class="mem-content">{{ m.content }}</div>
      <div class="actions">
        <button v-if="m.status !== 'confirmed'" type="button" @click="emit('confirm', m.id)">
          确认
        </button>
        <button type="button" @click="startEdit(m)">编辑</button>
        <button type="button" class="danger" @click="emit('remove', m.id)">删除</button>
      </div>
    </div>

    <div class="add-row">
      <select v-model="newType">
        <option v-for="t in TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <input v-model="newContent" type="text" placeholder="如：喜欢历史文化" @keydown.enter="submitNew" />
      <button type="button" @click="submitNew">添加</button>
    </div>
  </div>
</template>

<style scoped>
.memory-panel {
  padding: 4px 14px 16px;
}
.mp-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sub);
  padding: 8px 0;
}
.hint {
  font-weight: 400;
  margin-left: 6px;
  color: #9aa0a6;
}
.empty {
  font-size: 12px;
  color: var(--text-sub);
}
.mem-item {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.mem-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.tag {
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 999px;
  padding: 1px 8px;
}
.badge {
  font-size: 11px;
  color: #c2680b;
}
.badge.confirmed {
  color: #12805c;
}
.mem-content {
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}
.actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.actions button,
.edit-row button,
.add-row button {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 7px;
  font-size: 12px;
  padding: 3px 9px;
  cursor: pointer;
}
.actions button.danger {
  color: #d93026;
}
.edit-row {
  display: flex;
  gap: 6px;
}
.edit-row input,
.add-row input,
.add-row select {
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 4px 7px;
  font-size: 12px;
  min-width: 0;
}
.edit-row input {
  flex: 1;
}
.add-row {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}
.add-row input {
  flex: 1;
}
</style>
