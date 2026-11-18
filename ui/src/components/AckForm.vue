<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ACK_REASONS, today, type AckReasonCode, type AckRecord } from '../utils/ack'

const emit = defineEmits<{
  'action:submit': [record: AckRecord]
  'action:back': []
}>()

const root = ref<HTMLElement | null>(null)
const code = ref<AckReasonCode | null>(null)
const note = ref('')

const valid = computed(() => code.value !== null && note.value.trim().length > 0)

const hint = computed(() =>
  valid.value ? '提交后写入发布记录，可随时查看' : '选择原因码并填写备注后可提交',
)

onMounted(() => {
  // 打开表单时焦点落在原因码，避免直接落在提交按钮
  root.value?.querySelector<HTMLInputElement>('input[type="radio"]')?.focus()
})

function submit() {
  if (!valid.value || code.value === null) return
  emit('action:submit', { code: code.value, note: note.value.trim(), date: today() })
}
</script>

<template>
  <section ref="root" class="ack" aria-label="豁免表单">
    <p class="label">Reason · 豁免原因码</p>

    <ul class="ack__reasons">
      <li v-for="reason in ACK_REASONS" :key="reason.code">
        <label class="reason">
          <input v-model="code" type="radio" name="ack-reason" :value="reason.code" />
          <span class="reason__mark" aria-hidden="true" />
          <span class="reason__body">
            <span class="reason__line">
              <span class="reason__label">{{ reason.label }}</span>
              <span class="reason__code mono">{{ reason.code }}</span>
            </span>
            <span class="reason__hint">{{ reason.hint }}</span>
          </span>
        </label>
      </li>
    </ul>

    <div class="ack__note">
      <label class="label" for="ack-note">Note · 备注（必填）</label>
      <textarea
        id="ack-note"
        v-model="note"
        class="ack__input"
        rows="3"
        maxlength="200"
        placeholder="说明为何在未达门槛时仍要发布"
      />
      <p class="ack__count mono" aria-hidden="true">{{ note.length }} / 200</p>
    </div>

    <p class="ack__hint" :class="{ 'is-ready': valid }">{{ hint }}</p>

    <div class="ack__actions">
      <button type="button" class="btn" @click="emit('action:back')">返回决策</button>
      <button type="button" class="btn btn--primary" :disabled="!valid" @click="submit">
        提交豁免
      </button>
    </div>
  </section>
</template>

<style scoped>
.ack {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.ack__reasons {
  margin-top: calc(var(--sp-1) * -1);
}

.reason {
  position: relative;
  display: grid;
  grid-template-columns: 16px 1fr;
  align-items: start;
  gap: var(--sp-1);
  min-height: 44px;
  padding: var(--sp-1) 0;
  border-bottom: var(--hairline-w) solid var(--hairline);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.reason:hover,
.reason:focus-within {
  background: var(--blue-wash);
}

.reason input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* 自绘圆点：细线风格，不引入填充卡片 */
.reason__mark {
  position: relative;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border: var(--hairline-w) solid var(--ink-3);
  border-radius: 50%;
  transition: border-color var(--dur-fast) var(--ease-out);
}

.reason input:checked + .reason__mark {
  border-color: var(--ink);
}

.reason input:checked + .reason__mark::after {
  content: "";
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--ink);
}

.reason input:focus-visible + .reason__mark {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}

.reason__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.reason__line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
}

.reason__label {
  font-size: var(--fs-sub);
  color: var(--ink);
}

.reason__code {
  font-size: var(--fs-label);
  letter-spacing: 0.04em;
  color: var(--ink-3);
  white-space: nowrap;
}

.reason__hint {
  font-size: var(--fs-label-lg);
  color: var(--ink-3);
}

.ack__note {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ack__input {
  width: 100%;
  padding: var(--sp-1) 0;
  border: 0;
  border-bottom: var(--hairline-w) solid var(--hairline);
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-sub);
  line-height: 1.5;
  resize: none;
  transition: border-color var(--dur-fast) var(--ease-out);
}

.ack__input::placeholder {
  color: var(--ink-3);
}

.ack__input:focus-visible {
  border-bottom-color: var(--ink);
  background: var(--blue-wash);
  outline: none;
}

.ack__count {
  align-self: flex-end;
  font-size: var(--fs-label);
  color: var(--ink-3);
}

.ack__hint {
  font-size: var(--fs-label-lg);
  color: var(--ink-3);
}

.ack__hint.is-ready {
  color: var(--ink-2);
}

.ack__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-1);
  padding-top: var(--sp-2);
  border-top: var(--hairline-w) solid var(--hairline);
}
</style>
