<script setup>
const INTERESTS = [
  '历史文化',
  '自然风光',
  '红色旅游',
  '山岳',
  '古城',
  '宗教',
  '古建筑',
  '影视',
  '森林',
]

defineProps({
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])
const form = defineModel({ required: true })

function toggleInterest(name) {
  const arr = form.value.interests || []
  form.value.interests = arr.includes(name)
    ? arr.filter((x) => x !== name)
    : [...arr, name]
}
</script>

<template>
  <div class="plan-form">
    <div class="pf-title">基本信息</div>

    <label>出发位置</label>
    <input v-model="form.departure" type="text" placeholder="如：石家庄裕华区" />

    <div class="row">
      <div class="col">
        <label>出行日期</label>
        <input v-model="form.travel_date" type="date" />
      </div>
      <div class="col">
        <label>出发时间</label>
        <input v-model="form.start_time" type="time" />
      </div>
    </div>

    <div class="row">
      <div class="col">
        <label>出行人数</label>
        <input v-model.number="form.people_count" type="number" min="1" max="50" />
      </div>
      <div class="col">
        <label>总预算（元）</label>
        <input v-model.number="form.budget" type="number" min="1" />
      </div>
    </div>

    <label>兴趣偏好</label>
    <div class="chips">
      <button
        v-for="i in INTERESTS"
        :key="i"
        type="button"
        :class="{ active: (form.interests || []).includes(i) }"
        @click="toggleInterest(i)"
      >
        {{ i }}
      </button>
    </div>

    <label>体力强度（1 轻松 - 5 挑战）</label>
    <input v-model.number="form.physical_level" type="range" min="1" max="5" />
    <div class="range-val">当前：{{ form.physical_level }}</div>

    <label class="check"><input v-model="form.has_elderly" type="checkbox" /> 有老人同行</label>
    <label class="check"><input v-model="form.has_children" type="checkbox" /> 有儿童同行</label>

    <label>最晚返回时间</label>
    <input v-model="form.latest_return_time" type="time" />

    <button class="submit" type="button" :disabled="disabled" @click="emit('submit')">
      {{ disabled ? '生成中…' : '生成行程' }}
    </button>
  </div>
</template>

<style scoped>
.plan-form {
  padding: 0 16px 20px;
}
.pf-title {
  font-weight: 600;
  margin-bottom: 10px;
  color: var(--text-sub);
  font-size: 13px;
}
label {
  display: block;
  font-size: 13px;
  color: var(--text-sub);
  margin: 12px 0 4px;
}
input[type='text'],
input[type='number'],
input[type='date'],
input[type='time'] {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}
input[type='text']:focus,
input[type='number']:focus,
input[type='date']:focus,
input[type='time']:focus {
  border-color: var(--accent);
}
.row {
  display: flex;
  gap: 10px;
}
.col {
  flex: 1;
}
input[type='range'] {
  width: 100%;
}
.range-val {
  font-size: 12px;
  color: var(--text-sub);
  text-align: right;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chips button {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
}
.chips button.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.check {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text);
}
.check input {
  width: auto;
}
.submit {
  width: 100%;
  margin-top: 16px;
  padding: 11px;
  border: none;
  background: var(--accent);
  color: #fff;
  border-radius: 10px;
  font-size: 15px;
  cursor: pointer;
}
.submit:disabled {
  background: #b6c4e8;
  cursor: not-allowed;
}
</style>