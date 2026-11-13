<script setup>
import { ref } from 'vue'
import { login, register } from '../api.js'

const emit = defineEmits(['authed'])
const mode = ref('login')
const email = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    const user =
      mode.value === 'login'
        ? await login(email.value.trim(), password.value)
        : await register(email.value.trim(), password.value, displayName.value.trim())
    emit('authed', user)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>石游助手</h1>
      <p class="sub">石家庄周边一日游智能规划 · 登录后你的会话与偏好会被安全保存</p>

      <div class="tabs">
        <button :class="{ active: mode === 'login' }" type="button" @click="mode = 'login'">
          登录
        </button>
        <button :class="{ active: mode === 'register' }" type="button" @click="mode = 'register'">
          注册
        </button>
      </div>

      <label>邮箱</label>
      <input v-model="email" type="email" placeholder="you@example.com" autocomplete="email" />

      <label>密码</label>
      <input
        v-model="password"
        type="password"
        placeholder="至少 8 位"
        :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
        @keydown.enter.prevent="submit"
      />

      <template v-if="mode === 'register'">
        <label>昵称（可选）</label>
        <input v-model="displayName" type="text" placeholder="怎么称呼你" />
      </template>

      <p v-if="error" class="error">{{ error }}</p>

      <button class="primary" type="button" :disabled="busy" @click="submit">
        {{ busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #eef3ff 0%, #f7f8fa 60%);
}
.auth-card {
  width: 380px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 28px 26px;
  box-shadow: 0 12px 40px rgba(31, 35, 41, 0.08);
}
h1 {
  margin: 0 0 6px;
  font-size: 22px;
}
.sub {
  margin: 0 0 18px;
  color: var(--text-sub);
  font-size: 13px;
  line-height: 1.6;
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.tabs button {
  flex: 1;
  padding: 9px;
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 10px;
  cursor: pointer;
  font-size: 14px;
}
.tabs button.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
label {
  display: block;
  font-size: 13px;
  color: var(--text-sub);
  margin: 14px 0 5px;
}
input {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 9px;
  font-size: 14px;
  outline: none;
}
input:focus {
  border-color: var(--accent);
}
.error {
  color: #d93026;
  font-size: 13px;
  margin: 12px 0 0;
}
.primary {
  width: 100%;
  margin-top: 18px;
  padding: 11px;
  border: none;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 15px;
  cursor: pointer;
}
.primary:disabled {
  background: #b6c4e8;
  cursor: not-allowed;
}
</style>
