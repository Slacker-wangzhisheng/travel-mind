<script setup>
defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
})
const emit = defineEmits(['select', 'remove'])

function fmt(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`
}
</script>

<template>
  <div class="session-list">
    <div class="sl-title">历史会话</div>
    <p v-if="!sessions.length" class="empty">还没有历史会话</p>
    <div
      v-for="s in sessions"
      :key="s.id"
      class="session-item"
      :class="{ active: s.id === activeId }"
      @click="emit('select', s.id)"
    >
      <div class="meta">
        <div class="name">{{ s.title }}</div>
        <div class="time">{{ fmt(s.updated_at) }}</div>
      </div>
      <button type="button" class="del" title="删除会话" @click.stop="emit('remove', s.id)">
        ✕
      </button>
    </div>
  </div>
</template>

<style scoped>
.session-list {
  padding: 4px 12px 12px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}
.sl-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sub);
  padding: 6px 4px;
}
.empty {
  font-size: 12px;
  color: var(--text-sub);
  padding: 0 4px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 9px;
  border-radius: 9px;
  cursor: pointer;
}
.session-item:hover {
  background: #f2f3f5;
}
.session-item.active {
  background: var(--accent-soft);
}
.meta {
  flex: 1;
  min-width: 0;
}
.name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 2px;
}
.del {
  border: none;
  background: none;
  color: var(--text-sub);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 4px;
}
.del:hover {
  color: #d93026;
}
</style>
