<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, putJSON } from '../api'

const rows = ref([])
const changes = ref([])
const err = ref('')
const msg = ref('')
const busyId = ref(null)

const MODES = [
  {
    value: 'patch',
    title: '字段级补丁',
    desc: '只更新事件载荷里出现的键；未出现字段保留投影原值。',
  },
  {
    value: 'replace',
    title: '整行替换',
    desc: '事件载荷成为该实体完整新行；未出现字段视为清除。',
  },
]

async function refresh() {
  rows.value = await getJSON('/api/streams')
  changes.value = []
  for (const s of rows.value) {
    try {
      const p = await getJSON(`/api/streams/${s.id}/policy`)
      changes.value.push(...p.mode_changes.map((c) => ({ ...c, stream_title: s.title })))
    } catch (e) {
      err.value = String(e.message || e)
    }
  }
  changes.value.sort((a, b) => b.id - a.id)
}

async function switchMode(stream, mode) {
  err.value = ''
  msg.value = ''
  busyId.value = stream.id
  try {
    const r = await putJSON(`/api/streams/${stream.id}/policy`, {
      upsert_mode: mode,
      note: '页面切换策略',
    })
    msg.value = r.changed
      ? `${stream.title} 已切到「${mode === 'patch' ? '字段级补丁' : '整行替换'}」：${
          r.effective_after_batch_id == null
            ? '本次为首个批次，之后新开批次生效'
            : `对批次 #${r.effective_after_batch_id} 之后的新批次生效`
        }`
      : `${stream.title} 策略未变化`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busyId.value = null
  }
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>
<template>
  <div class="page">
    <h1>投影流</h1>
    <p class="muted">
      每条流绑定 CDC 夹具、实体键名与载荷字段，并独立配置 upsert 合并策略。同一流同一时刻只能一种策略；
      批次进行中禁止切换，切换仅对之后新开批次生效，不改变历史批次口径。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel">
      <h3 style="margin-top:0">合并策略配置</h3>
      <table>
        <thead>
          <tr>
            <th>投影流</th><th>实体键</th><th>载荷字段</th><th>当前策略</th><th>切换</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td>{{ s.title }}<div class="muted mono" style="font-size:0.8rem">{{ s.code }}</div></td>
            <td class="mono">{{ s.entity_key_name }}</td>
            <td class="mono">{{ s.payload_fields.join(', ') }}</td>
            <td>
              <span class="tag" :class="s.upsert_mode === 'patch' ? 'ok' : 'warn'">
                {{ s.upsert_mode === 'patch' ? '字段级补丁' : '整行替换' }}
              </span>
              <div class="muted" style="font-size:0.8rem;max-width:20rem">{{ s.upsert_mode_label }}</div>
            </td>
            <td>
              <div style="display:flex;gap:0.35rem;flex-wrap:wrap">
                <button
                  v-for="m in MODES"
                  :key="m.value"
                  :disabled="busyId === s.id || s.upsert_mode === m.value || s.has_running_batch"
                  @click="switchMode(s, m.value)"
                  :title="s.has_running_batch ? '批次进行中，禁止改策略' : m.desc"
                  style="padding:0.32rem 0.6rem;font-size:0.82rem"
                >
                  {{ m.title }}
                </button>
              </div>
            </td>
            <td>
              <span v-if="s.has_running_batch" class="tag danger">批次进行中</span>
              <span v-else class="tag">可切换</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">策略变更留痕</h3>
      <table>
        <thead>
          <tr><th>流</th><th>从</th><th>到</th><th>生效截止批次</th><th>备注</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in changes" :key="c.id">
            <td>{{ c.stream_title }}</td>
            <td class="mono">{{ c.from_mode || '—' }}</td>
            <td class="mono">{{ c.to_mode }}</td>
            <td class="mono">{{ c.cutoff_batch_id ?? '—' }}</td>
            <td class="muted">{{ c.note || '—' }}</td>
            <td class="mono">{{ c.changed_at }}</td>
          </tr>
          <tr v-if="!changes.length"><td colspan="6" class="muted">尚无策略变更，所有流保持种子策略。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
