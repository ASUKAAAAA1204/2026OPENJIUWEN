<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Claim, ClaimStatus, DimState } from '../api/types'

const props = defineProps<{
  open: boolean
  dimLabel: string
  dimNum: number
  dimDen: number
  dimState: DimState
  claims: Claim[]
}>()

const emit = defineEmits<{ 'action:close': [] }>()

const panelRef = ref<HTMLElement | null>(null)
const expandedId = ref<string | null>(null)
let restoreFocus: HTMLElement | null = null

const countText = computed(() => {
  if (props.dimState === 'NA') return '无适用项'
  if (props.dimState === 'MISSING') return '未采集'
  return `${props.dimNum}/${props.dimDen}`
})

const emptyText = computed(() => {
  if (props.dimState === 'NA') return '该维度无适用项，不产生结论'
  if (props.dimState === 'MISSING') return '该维度尚未采集，暂无结论'
  return '该维度暂无结论'
})

const STATUS_TEXT: Record<ClaimStatus, string> = {
  LOCATED: '已定位',
  UNLOCATED: '未定位',
  UNVERIFIABLE: '无法验证',
}

const STATUS_TONE: Record<ClaimStatus, string> = {
  LOCATED: 'ok',
  UNLOCATED: 'warn',
  UNVERIFIABLE: 'muted',
}

function toggle(id: string) {
  expandedId.value = expandedId.value === id ? null : id
}

function delay(i: number): string {
  return `${(i < 8 ? i * 30 : 0) + 'ms'}`
}

function close() {
  emit('action:close')
}

/** 焦点陷阱：Esc 关闭；Tab 在面板内循环 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    return
  }
  if (e.key !== 'Tab' || !panelRef.value) return
  const items = Array.from(
    panelRef.value.querySelectorAll<HTMLElement>('button, [href], [tabindex]:not([tabindex="-1"])'),
  )
  if (items.length === 0) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  if (e.shiftKey) {
    if (active === first || !panelRef.value.contains(active)) {
      e.preventDefault()
      last.focus()
    }
  } else if (active === last) {
    e.preventDefault()
    first.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      restoreFocus = document.activeElement as HTMLElement | null
      expandedId.value = null
      document.body.style.overflow = 'hidden'
      await nextTick()
      panelRef.value?.focus()
    } else {
      document.body.style.overflow = ''
      restoreFocus?.focus?.()
      restoreFocus = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="drawer">
        <div class="drawer__scrim" @click="close" />
        <aside
          ref="panelRef"
          class="drawer__panel"
          role="dialog"
          aria-modal="true"
          :aria-label="`${dimLabel} 维度证据，${countText}`"
          tabindex="-1"
          @keydown="onKeydown"
        >
          <header class="drawer__head">
            <div>
              <p class="label">Evidence · 证据</p>
              <h2 class="drawer__title serif">{{ dimLabel }}</h2>
              <p class="drawer__count mono">{{ countText }}</p>
            </div>
            <button type="button" class="btn" @click="close">关闭</button>
          </header>

          <p v-if="dimState !== 'VALUE' || claims.length === 0" class="drawer__empty">
            {{ emptyText }}
          </p>

          <ul v-else class="drawer__list">
            <li v-for="(claim, i) in claims" :key="claim.id" class="claim">
              <button
                type="button"
                class="claim__head"
                :aria-expanded="expandedId === claim.id"
                :aria-controls="`claim-body-${claim.id}`"
                @click="toggle(claim.id)"
                :style="{ animationDelay: delay(i) }"
              >
                <span class="claim__id mono">{{ claim.id }}</span>
                <span class="claim__text">{{ claim.text }}</span>
                <span class="claim__status" :class="`is-${STATUS_TONE[claim.status]}`">
                  <span class="claim__mark" aria-hidden="true" />
                  {{ STATUS_TEXT[claim.status] }}
                </span>
              </button>

              <div
                :id="`claim-body-${claim.id}`"
                class="claim__detail"
                :class="{ 'is-open': expandedId === claim.id }"
                :aria-hidden="expandedId !== claim.id"
              >
                <dl class="claim__dl">
                  <dt class="label label--latin">Locator</dt>
                  <dd class="mono" :class="{ 'is-missing': !claim.locator }">
                    {{ claim.locator ?? '缺失' }}
                  </dd>
                  <dt class="label label--latin">Digest</dt>
                  <dd class="mono">{{ claim.digest.slice(0, 8) }}</dd>
                  <dt class="label label--latin">Status</dt>
                  <dd>{{ STATUS_TEXT[claim.status] }}</dd>
                  <template v-if="claim.fail_code">
                    <dt class="label label--latin">Fail Code</dt>
                    <dd class="mono">{{ claim.fail_code }}</dd>
                  </template>
                </dl>
              </div>
            </li>
          </ul>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  justify-content: flex-end;
}

.drawer__scrim {
  position: absolute;
  inset: 0;
  background: var(--scrim);
  backdrop-filter: blur(8px);
}

.drawer__panel {
  position: relative;
  width: 480px;
  max-width: 100%;
  height: 100%;
  padding: var(--sp-4);
  overflow-y: auto;
  background: var(--bg);
  border-left: var(--hairline-w) solid var(--hairline);
  outline: none;
}

.drawer__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.drawer__title {
  margin-top: 4px;
  font-size: 22px;
  color: var(--ink);
}

.drawer__count {
  margin-top: 2px;
  font-size: var(--fs-sub);
  color: var(--ink-2);
}

.drawer__empty {
  padding: var(--sp-4) 0;
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

.drawer__list {
  margin-top: var(--sp-1);
}

.claim {
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.claim__head {
  display: grid;
  grid-template-columns: 52px 1fr 64px;
  align-items: baseline;
  gap: var(--sp-1);
  width: 100%;
  min-height: 40px;
  padding: var(--sp-1) 0;
  text-align: left;
  animation: claim-in 180ms var(--ease-out) both;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.claim__head:hover {
  background: var(--blue-wash);
}

@keyframes claim-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.claim__id {
  font-size: var(--fs-label-lg);
  color: var(--ink-3);
}

.claim__text {
  font-size: var(--fs-sub);
  color: var(--ink);
}

.claim__status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-label-lg);
  white-space: nowrap;
  justify-self: end;
}

.claim__mark {
  width: 4px;
  height: 4px;
  background: currentColor;
}

.claim__status.is-ok {
  color: var(--success);
}

.claim__status.is-warn {
  color: var(--warning);
}

.claim__status.is-muted {
  color: var(--ink-3);
}

/* 单条展开：grid-template-rows 0fr → 1fr，不逐帧改高度 */
.claim__detail {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur) var(--ease-in-out);
}

.claim__detail.is-open {
  grid-template-rows: 1fr;
}

.claim__dl {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 4px var(--sp-2);
  margin: 0;
  padding-bottom: 0;
  overflow: hidden;
  font-size: var(--fs-sub);
  color: var(--ink-2);
}

.claim__detail.is-open .claim__dl {
  padding-bottom: var(--sp-2);
}

.claim__dl dd {
  margin: 0;
  word-break: break-all;
}

.claim__dl .is-missing {
  color: var(--danger);
}

/* 抽屉 280ms spring；遮罩淡入 */
.drawer-enter-active .drawer__panel,
.drawer-leave-active .drawer__panel {
  transition:
    transform 280ms var(--ease-spring),
    opacity 280ms var(--ease-out);
}

.drawer-enter-from .drawer__panel,
.drawer-leave-to .drawer__panel {
  transform: translateX(100%);
  opacity: 0.6;
}

.drawer-enter-active .drawer__scrim,
.drawer-leave-active .drawer__scrim {
  transition: opacity var(--dur-slow) var(--ease-out);
}

.drawer-enter-from .drawer__scrim,
.drawer-leave-to .drawer__scrim {
  opacity: 0;
}
</style>
