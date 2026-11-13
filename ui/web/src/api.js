// 生产环境可将 VITE_API_TARGET 指向后端源（如 http://localhost:8000），
// 开发环境留空即可，由 Vite 将 /api 代理到后端。
const target = (import.meta.env.VITE_API_TARGET || '').replace(/\/$/, '')

async function request(path, { method = 'GET', body } = {}) {
  const resp = await fetch(`${target}${path}`, {
    method,
    credentials: 'include', // 关键：携带 HttpOnly 认证 Cookie
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    let detail = text
    try {
      detail = JSON.parse(text).detail || text
    } catch {
      /* 保留原始文本 */
    }
    throw new Error(`请求失败 (${resp.status}) ${detail}`)
  }
  if (resp.status === 204) return null
  return resp.json()
}

// ---------------- 认证 ----------------
export const register = (email, password, displayName) =>
  request('/api/v1/auth/register', {
    method: 'POST',
    body: { email, password, display_name: displayName || null },
  })

export const login = (email, password) =>
  request('/api/v1/auth/login', { method: 'POST', body: { email, password } })

export const logout = () => request('/api/v1/auth/logout', { method: 'POST' })

export const me = () => request('/api/v1/auth/me')

// ---------------- 对话 ----------------
// 多轮对话：首轮带 form，后续轮带 session_id。
export function chat(message, sessionId, form) {
  return request('/api/v1/chat', {
    method: 'POST',
    body: { message, session_id: sessionId || null, form: form || null },
  })
}

// ---------------- 历史会话 ----------------
export const listSessions = () => request('/api/v1/chat/sessions')
export const getSession = (id) => request(`/api/v1/chat/sessions/${id}`)
export const deleteSession = (id) =>
  request(`/api/v1/chat/sessions/${id}`, { method: 'DELETE' })

// ---------------- 长期记忆 ----------------
export const listMemories = () => request('/api/v1/memories')
export const addMemory = (type, content) =>
  request('/api/v1/memories', { method: 'POST', body: { type, content } })
export const updateMemory = (id, payload) =>
  request(`/api/v1/memories/${id}`, { method: 'PATCH', body: payload })
export const deleteMemory = (id) =>
  request(`/api/v1/memories/${id}`, { method: 'DELETE' })
