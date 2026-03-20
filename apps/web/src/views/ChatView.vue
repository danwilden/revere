<template>
  <div class="chat-layout">

    <!-- ===== LEFT PANEL: Session list ===== -->
    <aside class="sessions-panel">
      <div class="sessions-panel__header">
        <span class="nb-label sessions-panel__title">SESSIONS</span>
        <button
          class="nb-btn nb-btn--primary sessions-panel__new-btn"
          :disabled="store.loadingSessions"
          @click="store.createSession()"
        >
          + NEW
        </button>
      </div>

      <div class="sessions-panel__body">
        <LoadingState v-if="store.loadingSessions && !store.sessions.length" message="LOADING..." />

        <div v-else-if="!store.sessions.length" class="sessions-empty">
          <span class="nb-label text-dim">NO SESSIONS</span>
        </div>

        <div v-else class="sessions-list">
          <div
            v-for="session in store.sessions"
            :key="session.id"
            class="session-item"
            :class="{ 'session-item--active': session.id === store.activeSessionId }"
          >
            <button
              type="button"
              class="session-item__main"
              @click="store.selectSession(session.id)"
            >
              <span class="session-item__id font-mono">{{ truncateId(session.id) }}</span>
              <span class="session-item__meta nb-label">
                {{ formatDate(session.created_at) }}
                <span v-if="session.message_count != null" class="session-item__count">
                  — {{ session.message_count }}
                </span>
              </span>
            </button>
            <button
              type="button"
              class="session-item__delete"
              aria-label="Delete conversation"
              @click.stop="onDeleteSession(session.id)"
            >
              ×
            </button>
          </div>
        </div>
      </div>
    </aside>

    <!-- ===== MAIN AREA ===== -->
    <main class="chat-main">

      <!-- Error banner -->
      <div v-if="store.error" class="nb-banner nb-banner--error error-banner-row">
        <span>ERR // {{ store.error }}</span>
        <button class="nb-btn" style="font-size: 10px; padding: 2px 8px" @click="store.error = null">
          DISMISS
        </button>
      </div>

      <!-- No session selected -->
      <template v-if="!store.activeSessionId">
        <div class="chat-no-session">
          <EmptyState message="SELECT A SESSION OR CREATE NEW" />
        </div>
      </template>

      <!-- Active session -->
      <template v-else>
        <div class="chat-session-layout">

          <!-- Message thread -->
          <div ref="threadRef" class="message-thread">
            <LoadingState v-if="store.loadingMessages && !store.messages.length" message="LOADING MESSAGES..." />

            <EmptyState
              v-else-if="!store.messages.length && !store.isStreaming"
              message="NO MESSAGES YET — TYPE BELOW TO START"
            />

            <template v-else>
              <!-- Rendered messages -->
              <div
                v-for="msg in store.messages"
                :key="msg.id"
                class="message-row"
                :class="msg.role === 'user' ? 'message-row--user' : 'message-row--assistant'"
              >
                <div class="message-bubble nb-card" :class="messageBubbleClass(msg.role)">
                  <!-- Role label -->
                  <div class="message-bubble__role nb-label">
                    {{ msg.role === 'user' ? 'YOU' : 'MEDALLION AI' }}
                    <span class="message-bubble__ts text-dim">
                      {{ formatTime(msg.created_at) }}
                    </span>
                  </div>

                  <!-- Rendered content -->
                  <div class="message-bubble__body" v-html="renderContent(msg.content)" />

                  <!-- "RUN STRATEGY" inline action for strategy JSON blocks -->
                  <div
                    v-if="msg.role === 'assistant' && hasStrategyBlock(msg.content)"
                    class="message-bubble__action"
                  >
                    <button
                      class="nb-btn"
                      style="font-size: 10px; padding: 4px 12px"
                      @click="onRunStrategy(msg)"
                    >
                      RUN STRATEGY
                    </button>
                  </div>

                  <!-- "VIEW RESULTS" link for completed backtests -->
                  <div
                    v-if="msg.role === 'assistant' && hasBacktestCompleteAction(msg)"
                    class="message-bubble__action"
                  >
                    <button
                      class="nb-btn nb-btn--primary"
                      style="font-size: 10px; padding: 4px 12px"
                      @click="onViewResults(msg)"
                    >
                      VIEW RESULTS
                    </button>
                  </div>
                </div>
              </div>

              <!-- Streaming assistant message -->
              <div v-if="store.isStreaming || store.streamingContent" class="message-row message-row--assistant">
                <div class="message-bubble nb-card">
                  <div class="message-bubble__role nb-label">
                    MEDALLION AI
                    <span class="status-chip status-chip--running" style="margin-left: 8px">
                      <span class="status-dot" />
                      STREAMING
                    </span>
                  </div>
                  <div class="message-bubble__body message-bubble__body--streaming">
                    <span v-html="renderContent(store.streamingContent)" />
                    <span class="stream-cursor">_</span>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <!-- Pending action confirmation card -->
          <div v-if="store.pendingAction" class="pending-action-card">
            <div class="pending-action-card__header">
              <span class="nb-label pending-action-card__title">PROPOSED ACTION</span>
              <span class="pending-action-card__type font-mono">
                {{ store.pendingAction.action_type.replace(/_/g, ' ').toUpperCase() }}
              </span>
            </div>
            <div class="pending-action-card__desc font-mono">
              {{
                store.pendingAction.payload?.description
                  ?? store.pendingAction.payload?.strategy_name
                  ?? store.pendingAction.action_type
              }}
            </div>
            <div class="pending-action-card__btns">
              <button
                class="nb-btn nb-btn--primary pending-action-card__yes"
                :disabled="store.isStreaming"
                @click="confirmAction"
              >
                YES — RUN IT
              </button>
              <button
                class="nb-btn nb-btn--danger pending-action-card__no"
                :disabled="store.isStreaming"
                @click="declineAction"
              >
                NO — DISCARD
              </button>
            </div>
          </div>

          <!-- Input area -->
          <div class="input-area">
            <!-- Context expand toggle -->
            <div class="context-row">
              <button
                class="nb-btn context-toggle"
                style="font-size: 10px; padding: 3px 10px"
                @click="showContext = !showContext"
              >
                {{ showContext ? 'HIDE CONTEXT' : 'SET CONTEXT' }}
              </button>
              <div v-if="context.experiment_id || context.strategy_id" class="context-pills">
                <span v-if="context.experiment_id" class="context-pill">
                  EXP: {{ context.experiment_id }}
                </span>
                <span v-if="context.strategy_id" class="context-pill">
                  STRAT: {{ context.strategy_id }}
                </span>
              </div>
            </div>

            <!-- Context fields (collapsible) -->
            <div v-show="showContext" class="context-fields">
              <div class="context-field-group">
                <label class="nb-label">EXPERIMENT ID</label>
                <input
                  v-model="context.experiment_id"
                  class="nb-text-input font-mono"
                  placeholder="exp_..."
                />
              </div>
              <div class="context-field-group">
                <label class="nb-label">STRATEGY ID</label>
                <input
                  v-model="context.strategy_id"
                  class="nb-text-input font-mono"
                  placeholder="strat_..."
                />
              </div>
            </div>

            <!-- Message compose -->
            <div class="compose-row">
              <textarea
                ref="textareaRef"
                v-model="draftContent"
                class="compose-textarea font-mono"
                placeholder="TYPE A MESSAGE..."
                rows="3"
                :disabled="store.isStreaming"
                @keydown.enter.exact.prevent="handleSend"
                @keydown.enter.shift.exact="() => {}"
              />
              <div class="compose-actions">
                <button
                  class="nb-btn nb-btn--primary compose-send"
                  :disabled="!canSend"
                  @click="handleSend"
                >
                  {{ store.isStreaming ? 'STREAMING...' : 'SEND' }}
                </button>
                <button
                  v-if="store.isStreaming"
                  class="nb-btn nb-btn--danger compose-cancel"
                  @click="store.cancelStream()"
                >
                  CANCEL
                </button>
              </div>
            </div>
          </div>

        </div>
      </template>
    </main>

  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chat.js'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

// ---------------------------------------------------------------------------
// Store + Router
// ---------------------------------------------------------------------------

const store = useChatStore()
const router = useRouter()

// ---------------------------------------------------------------------------
// Local state
// ---------------------------------------------------------------------------

const draftContent = ref('')
const showContext = ref(false)
const context = ref({ experiment_id: '', strategy_id: '' })
const threadRef = ref(null)
const textareaRef = ref(null)

// ---------------------------------------------------------------------------
// Derived
// ---------------------------------------------------------------------------

const canSend = computed(() =>
  !store.isStreaming &&
  !!store.activeSessionId &&
  draftContent.value.trim().length > 0,
)

// ---------------------------------------------------------------------------
// Content rendering helpers
// ---------------------------------------------------------------------------

/**
 * Minimal markdown-ish renderer:
 *   - Fenced code blocks (```...```) → <pre><code>
 *   - **bold** → <strong>
 *   - Escapes HTML in non-code segments to prevent XSS
 */
function renderContent(raw) {
  if (!raw) return ''

  const parts = []
  // Split on fenced code blocks
  const segments = raw.split(/(```[\s\S]*?```)/g)

  for (const seg of segments) {
    if (seg.startsWith('```') && seg.endsWith('```')) {
      // Code block: strip the backticks + optional language hint on first line
      const inner = seg.slice(3, -3)
      const firstNewline = inner.indexOf('\n')
      const code = firstNewline >= 0 ? inner.slice(firstNewline + 1) : inner
      parts.push(`<pre class="code-block"><code>${escapeHtml(code.trimEnd())}</code></pre>`)
    } else {
      // Regular text: escape then apply inline markup
      let escaped = escapeHtml(seg)
      // Bold: **text**
      escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      parts.push(escaped)
    }
  }

  // Wrap in a span to preserve whitespace for non-code segments
  return `<span class="msg-text">${parts.join('')}</span>`
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * Detect whether an assistant message contains a strategy JSON block
 * (has "entry_long" or "entry_short" keys).
 */
function hasStrategyBlock(content) {
  if (!content) return false
  return /["']?entry_long["']?\s*:|["']?entry_short["']?\s*:/.test(content)
}

/**
 * Returns true if an assistant message contains a backtest_complete action.
 */
function hasBacktestCompleteAction(msg) {
  return (msg.actions_json ?? []).some((a) => a.action_type === 'backtest_complete')
}

/**
 * Navigate to the Results view for a completed backtest.
 */
function onViewResults(msg) {
  const action = (msg.actions_json ?? []).find((a) => a.action_type === 'backtest_complete')
  const runId = action?.payload?.run_id
  if (runId) {
    router.push({ name: 'Results', query: { runId } })
  }
}

/**
 * "RUN STRATEGY" inline button — treat as a "yes" confirmation to the agent.
 */
async function onRunStrategy(_msg) {
  const ctx = {
    experiment_id: context.value.experiment_id.trim() || undefined,
    strategy_id: context.value.strategy_id.trim() || undefined,
  }
  await store.sendMessage('yes', ctx)
  store.pendingAction = null
}

/**
 * Confirmation card — YES handler.
 */
async function confirmAction() {
  const ctx = {
    experiment_id: context.value.experiment_id.trim() || undefined,
    strategy_id: context.value.strategy_id.trim() || undefined,
  }
  await store.sendMessage('yes', ctx)
  store.pendingAction = null
}

/**
 * Confirmation card — NO handler.
 */
async function declineAction() {
  const ctx = {
    experiment_id: context.value.experiment_id.trim() || undefined,
    strategy_id: context.value.strategy_id.trim() || undefined,
  }
  await store.sendMessage('no', ctx)
  store.pendingAction = null
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function truncateId(id) {
  if (!id) return '—'
  return id.length > 16 ? `${id.slice(0, 8)}...${id.slice(-4)}` : id
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return ''
  }
}

function onDeleteSession(sessionId) {
  if (confirm('Delete this conversation?')) {
    store.deleteSession(sessionId)
  }
}

function messageBubbleClass(role) {
  if (role === 'user') return 'message-bubble--user nb-card--accent'
  return ''
}

// ---------------------------------------------------------------------------
// Scroll to bottom on new messages / streaming tokens
// ---------------------------------------------------------------------------

function scrollToBottom() {
  nextTick(() => {
    if (threadRef.value) {
      threadRef.value.scrollTop = threadRef.value.scrollHeight
    }
  })
}

watch(() => store.messages.length, scrollToBottom)
watch(() => store.streamingContent, scrollToBottom)

// ---------------------------------------------------------------------------
// Send
// ---------------------------------------------------------------------------

function handleSend() {
  if (!canSend.value) return
  const trimmed = draftContent.value.trim()
  if (!trimmed) return

  const ctx = {
    experiment_id: context.value.experiment_id.trim() || undefined,
    strategy_id: context.value.strategy_id.trim() || undefined,
  }

  store.sendMessage(trimmed, ctx)
  draftContent.value = ''
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  store.fetchSessions()
})

onUnmounted(() => {
  store.cancelStream()
  store.stopJobPolling()
})
</script>

<style scoped>
/* =====================================================================
   LAYOUT
   ===================================================================== */

.chat-layout {
  display: flex;
  height: 100%;
  min-height: 0;
  background: var(--clr-bg);
}

/* =====================================================================
   LEFT PANEL — Sessions
   ===================================================================== */

.sessions-panel {
  width: 220px;
  flex-shrink: 0;
  border-right: 2px solid var(--clr-border);
  background: var(--clr-surface);
  display: flex;
  flex-direction: column;
}

.sessions-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 14px 12px;
  border-bottom: 2px solid var(--clr-border);
  flex-shrink: 0;
}

.sessions-panel__title {
  letter-spacing: 0.14em;
  color: var(--clr-text-muted);
}

.sessions-panel__new-btn {
  font-size: 10px;
  padding: 4px 10px;
}

.sessions-panel__body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 0;
}

.sessions-empty {
  padding: 24px 14px;
  text-align: center;
}

.sessions-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px;
}

.session-item {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-left: 3px solid transparent;
  width: 100%;
  color: var(--clr-text-muted);
  transition: background 80ms, border-color 80ms, color 80ms;
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.03);
  border-color: var(--clr-border);
  border-left-color: var(--clr-border-bright);
  color: var(--clr-text);
}

.session-item--active {
  background: rgba(255, 230, 0, 0.06) !important;
  border-left-color: var(--clr-yellow) !important;
  border-color: var(--clr-border) !important;
  color: var(--clr-text) !important;
}

.session-item__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 10px;
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
  width: 100%;
  color: inherit;
  font: inherit;
}

.session-item__delete {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  padding: 0;
  margin-right: 6px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 2px;
  color: var(--clr-text-dim);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: color 80ms, background 80ms;
}

.session-item__delete:hover {
  color: var(--clr-text);
  background: rgba(255, 255, 255, 0.08);
}

.session-item__id {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-item--active .session-item__id {
  color: var(--clr-yellow);
}

.session-item__meta {
  font-size: 10px;
  letter-spacing: 0.06em;
}

.session-item__count {
  color: var(--clr-text-dim);
}

/* =====================================================================
   MAIN AREA
   ===================================================================== */

.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.offline-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex-shrink: 0;
}

.offline-banner__icon {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 16px;
  color: var(--clr-orange);
  flex-shrink: 0;
  line-height: 1.5;
}

.error-banner-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  color: #ff8888;
}

.chat-no-session {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
}

.offline-placeholder {
  letter-spacing: 0.1em;
  font-size: 10px;
  text-align: center;
}

/* =====================================================================
   ACTIVE SESSION LAYOUT
   ===================================================================== */

.chat-session-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* =====================================================================
   MESSAGE THREAD
   ===================================================================== */

.message-thread {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--gap-md) var(--gap-lg);
  display: flex;
  flex-direction: column;
  gap: var(--gap-md);
  /* Ensure thread sticks to bottom as content grows */
  scroll-behavior: smooth;
}

.message-row {
  display: flex;
}

.message-row--user {
  justify-content: flex-end;
}

.message-row--assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 72%;
  min-width: 120px;
}

.message-bubble--user {
  border-color: var(--clr-yellow);
  box-shadow: var(--shadow-nb-yellow);
}

.message-bubble__role {
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.12em;
  margin-bottom: 8px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--clr-border);
  font-size: 10px;
}

.message-row--user .message-bubble__role {
  color: var(--clr-yellow);
}

.message-bubble__ts {
  margin-left: 6px;
  font-size: 9px;
  letter-spacing: 0.05em;
}

.message-bubble__body {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.65;
  color: var(--clr-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.message-bubble__body--streaming {
  /* Keep the mono spacing consistent while tokens arrive */
}

.message-bubble__action {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--clr-border);
}

/* Code blocks inside messages */
:deep(.code-block) {
  background: var(--clr-panel);
  border: 1px solid var(--clr-border);
  border-left: 3px solid var(--clr-yellow);
  padding: 10px 12px;
  margin: 8px 0;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre;
}

:deep(.code-block code) {
  font-family: inherit;
  font-size: inherit;
  background: none;
  padding: 0;
}

:deep(.msg-text strong) {
  color: var(--clr-yellow);
  font-weight: 700;
}

/* Streaming cursor blink */
.stream-cursor {
  display: inline-block;
  color: var(--clr-yellow);
  font-family: var(--font-mono);
  animation: cursor-blink 0.9s step-start infinite;
  font-weight: 700;
}

@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* =====================================================================
   INPUT AREA
   ===================================================================== */

.input-area {
  flex-shrink: 0;
  border-top: 2px solid var(--clr-border);
  background: var(--clr-surface);
  padding: var(--gap-md) var(--gap-lg);
  display: flex;
  flex-direction: column;
  gap: var(--gap-sm);
}

/* Context row */
.context-row {
  display: flex;
  align-items: center;
  gap: var(--gap-sm);
}

.context-toggle {
  font-size: 10px;
  padding: 3px 10px;
  flex-shrink: 0;
}

.context-pills {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.context-pill {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--clr-yellow);
  border: 1px solid var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
  padding: 2px 8px;
}

/* Context fields */
.context-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--gap-md);
  padding: var(--gap-sm) 0;
  border-top: 1px solid var(--clr-border);
}

.context-field-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nb-text-input {
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-size: 12px;
  padding: 6px 10px;
  outline: none;
  width: 100%;
  transition: border-color 80ms;
}

.nb-text-input:focus {
  border-color: var(--clr-yellow);
}

.nb-text-input::placeholder {
  color: var(--clr-text-dim);
}

/* Compose row */
.compose-row {
  display: flex;
  align-items: flex-end;
  gap: var(--gap-sm);
}

.compose-textarea {
  flex: 1;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-size: 13px;
  line-height: 1.5;
  padding: 10px 12px;
  resize: none;
  outline: none;
  min-height: 64px;
  transition: border-color 80ms;
}

.compose-textarea:focus {
  border-color: var(--clr-yellow);
}

.compose-textarea::placeholder {
  color: var(--clr-text-dim);
  letter-spacing: 0.08em;
}

.compose-textarea:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.compose-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

.compose-send {
  font-size: 12px;
  padding: 10px 20px;
  white-space: nowrap;
}

.compose-cancel {
  font-size: 11px;
  padding: 6px 14px;
}

/* =====================================================================
   PENDING ACTION CONFIRMATION CARD
   ===================================================================== */

.pending-action-card {
  flex-shrink: 0;
  border-top: 2px solid var(--clr-yellow);
  border-bottom: 2px solid var(--clr-border);
  background: rgba(255, 230, 0, 0.04);
  padding: 14px var(--gap-lg);
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: inset 0 2px 0 0 rgba(255, 230, 0, 0.08);
}

.pending-action-card__header {
  display: flex;
  align-items: center;
  gap: 14px;
}

.pending-action-card__title {
  letter-spacing: 0.14em;
  color: var(--clr-yellow);
  font-size: 11px;
}

.pending-action-card__type {
  font-size: 12px;
  font-weight: 700;
  color: var(--clr-text);
  border: 2px solid var(--clr-border);
  background: var(--clr-panel);
  padding: 2px 10px;
  box-shadow: 2px 2px 0 #000;
  letter-spacing: 0.06em;
}

.pending-action-card__desc {
  font-size: 12px;
  color: var(--clr-text-muted);
  line-height: 1.5;
  max-width: 600px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pending-action-card__btns {
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
}

.pending-action-card__yes {
  font-size: 11px;
  padding: 6px 16px;
}

.pending-action-card__no {
  font-size: 11px;
  padding: 6px 16px;
}
</style>
