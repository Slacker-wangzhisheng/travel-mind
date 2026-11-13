<script setup>
import { reactive, ref, nextTick, onMounted } from 'vue'
import {
  addMemory,
  chat,
  deleteMemory,
  deleteSession,
  getSession,
  listMemories,
  listSessions,
  logout,
  me,
  updateMemory,
} from './api.js'
import AuthView from './components/AuthView.vue'
import PlanForm from './components/PlanForm.vue'
import ChatMessage from './components/ChatMessage.vue'
import SessionList from './components/SessionList.vue'
import MemoryPanel from './components/MemoryPanel.vue'

const SUGGESTIONS = [
  '不想去赵州桥',
  '最远单程不超过120公里',
  '路上有充电桩吗',
  '不去平山县',
]

function todayIso() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const form = reactive({
  departure: '石家庄裕华区',
  travel_date: todayIso(),
  start_time: '08:00',
  people_count: 2,
  budget: 600,
  interests: ['历史文化'],
  physical_level: 3,
  has_elderly: false,
  has_children: false,
  latest_return_time: '19:00',
})

const user = ref(null)
const booting = ref(true)
const sessions = ref([])
const memories = ref([])
const sessionId = ref(null)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const sidebarOpen = ref(false)
const chatEl = ref(null)

onMounted(async () => {
  try {
    user.value = await me()
    await refreshSidebar()
  } catch {
    user.value = null
  } finally {
    booting.value = false
  }
})

async function refreshSidebar() {
  try {
    const [ss, ms] = await Promise.all([listSessions(), listMemories()])
    sessions.value = ss
    memories.value = ms
  } catch {
    /* 未登录或服务暂不可用时忽略 */
  }
}

async function onAuthed(u) {
  user.value = u
  await refreshSidebar()
}

async function doLogout() {
  try {
    await logout()
  } finally {
    user.value = null
    sessions.value = []
    memories.value = []
    reset()
  }
}

function buildFormPayload() {
  return {
    departure: form.departure,
    travel_date: form.travel_date,
    people_count: form.people_count,
    budget: String(form.budget),
    interests: form.interests,
    transport_mode: 'driving',
    physical_level: form.physical_level,
    has_elderly: form.has_elderly,
    has_children: form.has_children,
    latest_return_time: form.latest_return_time,
    start_time: form.start_time,
  }
}

function summaryText() {
  const likes = form.interests.join('、') || '不限'
  return (
    `帮我规划石家庄周边一日游：从${form.departure}出发，${form.start_time}出发，` +
    `${form.people_count}人，预算${form.budget}元，偏好${likes}，最晚${form.latest_return_time}返回。`
  )
}

function scrollToBottom() {
  nextTick(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

async function run(message, payload) {
  sending.value = true
  draft.value = ''
  messages.value.push({ role: 'user', content: message })
  scrollToBottom()
  try {
    const data = await chat(message, sessionId.value, payload)
    sessionId.value = data.session_id
    messages.value.push({
      role: 'assistant',
      content: data.reply || '',
      itineraryOptions: data.itinerary_options || [],
      poiResults: data.poi_results || [],
      memorySuggestions: data.memory_suggestions || [],
    })
    await refreshSidebar()
  } catch (e) {
    messages.value.push({ role: 'assistant', content: `出错了：${e.message}` })
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function startPlanning() {
  run(summaryText(), buildFormPayload())
}

function sendFollowUp() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  run(text, null)
}

function quickSuggest(text) {
  draft.value = text
  sendFollowUp()
}

function reset() {
  sessionId.value = null
  messages.value = []
  draft.value = ''
}

async function openSession(id) {
  if (sending.value) return
  try {
    const detail = await getSession(id)
    sessionId.value = id
    const list = (detail.messages || []).map((m) => ({ role: m.role, content: m.content }))
    const lastAssistant = [...list].reverse().find((m) => m.role === 'assistant')
    if (lastAssistant && (detail.itinerary_options || []).length) {
      lastAssistant.itineraryOptions = detail.itinerary_options
    }
    messages.value = list
    sidebarOpen.value = false
    scrollToBottom()
  } catch (e) {
    messages.value.push({ role: 'assistant', content: `打开会话失败：${e.message}` })
  }
}

async function removeSession(id) {
  if (!window.confirm('确定删除该会话？删除后不可恢复。')) return
  try {
    await deleteSession(id)
    if (sessionId.value === id) reset()
    await refreshSidebar()
  } catch (e) {
    window.alert(`删除失败：${e.message}`)
  }
}

async function confirmMemory(id) {
  try {
    await updateMemory(id, { status: 'confirmed' })
    for (const m of messages.value) {
      for (const s of m.memorySuggestions || []) {
        if (s.id === id) s.status = 'confirmed'
      }
    }
    memories.value = await listMemories()
  } catch (e) {
    window.alert(`确认失败：${e.message}`)
  }
}

async function editMemory(id, payload) {
  try {
    await updateMemory(id, payload)
    memories.value = await listMemories()
  } catch (e) {
    window.alert(`修改失败：${e.message}`)
  }
}

async function removeMemory(id) {
  if (!window.confirm('确定删除该条长期偏好？')) return
  try {
    await deleteMemory(id)
    memories.value = await listMemories()
  } catch (e) {
    window.alert(`删除失败：${e.message}`)
  }
}

async function createMemory(type, content) {
  try {
    await addMemory(type, content)
    memories.value = await listMemories()
  } catch (e) {
    window.alert(`添加失败：${e.message}`)
  }
}
</script>

<template>
  <div v-if="booting" class="boot">正在加载…</div>

  <AuthView v-else-if="!user" @authed="onAuthed" />

  <div v-else class="app">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand-row">
        <span class="brand">石游助手</span>
        <button class="logout" type="button" @click="doLogout">退出</button>
      </div>
      <div class="user-line">{{ user.display_name || user.email }}</div>

      <button class="new-chat" type="button" @click="reset">＋ 新对话</button>

      <SessionList
        :sessions="sessions"
        :active-id="sessionId"
        @select="openSession"
        @remove="removeSession"
      />

      <PlanForm v-model="form" :disabled="sending" @submit="startPlanning" />

      <MemoryPanel
        :memories="memories"
        @confirm="confirmMemory"
        @update="editMemory"
        @remove="removeMemory"
        @add="createMemory"
      />
    </aside>

    <div class="chat-wrap">
      <header class="chat-header">
        <button class="menu-btn" type="button" @click="sidebarOpen = !sidebarOpen">☰</button>
        <span>石家庄周边一日游</span>
      </header>

      <main ref="chatEl" class="chat">
        <div class="chat-inner">
          <div v-if="messages.length === 0" class="welcome">
            <h2>今天想去哪里玩？</h2>
            <p>在左侧填写基本信息后点击「生成行程」，即可与 AI 对话式调整方案。</p>
            <div class="suggestions">
              <button v-for="s in SUGGESTIONS" :key="s" type="button" @click="quickSuggest(s)">
                {{ s }}
              </button>
            </div>
          </div>

          <ChatMessage
            v-for="(m, i) in messages"
            :key="i"
            :message="m"
            @confirm-memory="confirmMemory"
          />

          <div v-if="sending" class="typing">
            <div class="avatar">石</div>
            <span>正在思考…</span>
          </div>
        </div>
      </main>

      <footer class="input-bar">
        <div class="input-box">
          <textarea
            v-model="draft"
            rows="1"
            placeholder="继续调整行程，例如：不去赵州桥 / 最远单程不超过120公里 / 路上有充电桩吗"
            @keydown.enter.exact.prevent="sendFollowUp"
          ></textarea>
          <button type="button" :disabled="sending || !draft.trim()" @click="sendFollowUp">
            发送
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>
