<script setup>
import ItineraryCard from './ItineraryCard.vue'

defineProps({
  message: { type: Object, required: true },
})
defineEmits(['confirm-memory'])
</script>

<template>
  <div class="msg" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '我' : '石' }}</div>
    <div class="content">
      <div class="bubble">{{ message.content }}</div>

      <div v-if="(message.memorySuggestions || []).length" class="mem-suggest">
        <div class="ms-title">要把这些偏好记到长期记忆里吗？（需你确认后才会生效）</div>
        <div v-for="m in message.memorySuggestions" :key="m.id" class="ms-row">
          <span>{{ m.content }}</span>
          <button
            v-if="m.status !== 'confirmed'"
            type="button"
            @click="$emit('confirm-memory', m.id)"
          >
            记住
          </button>
          <span v-else class="done">已记住</span>
        </div>
      </div>

      <ItineraryCard
        v-for="(opt, i) in message.itineraryOptions || []"
        :key="i"
        :option="opt"
      />
      <ul v-if="(message.poiResults || []).length" class="poi-list">
        <li v-for="(p, i) in message.poiResults" :key="i">
          {{ p.name }}（{{ p.address || '地址未知' }}）
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.content {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: 100%;
}
.mem-suggest {
  margin-top: 10px;
  border: 1px dashed var(--accent);
  background: #f7f9ff;
  border-radius: 12px;
  padding: 10px 12px;
  max-width: 72%;
}
.ms-title {
  font-size: 12px;
  color: var(--text-sub);
  margin-bottom: 6px;
}
.ms-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  padding: 3px 0;
}
.ms-row button {
  border: 1px solid var(--accent);
  color: var(--accent);
  background: #fff;
  border-radius: 7px;
  font-size: 12px;
  padding: 2px 9px;
  cursor: pointer;
}
.ms-row .done {
  font-size: 12px;
  color: #12805c;
}
</style>
