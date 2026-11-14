<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const rows = ref([])
const batchId = ref(null)
const err = ref('')

function reasonName(r) {
  if (r === 'explicit_null') return '显式 null'
  if (r === 'missing') return '缺省清除'
  return r
}

function payloadParts(v) {
  if (v == null) return []
  if (typeof v !== 'object') return [String(v)]
  return Object.keys(v).map((k) => `${k}=${v[k] === null ? 'null' : JSON.stringify(v[k])}`)
}

function fullJson(v) {
  return v == null ? '' : JSON.stringify(v)
}

async function refresh() {
  const q = new URLSearchParams()
  if (batchId.value) q.set('batch_id', String(batchId.value))
  rows.value = await getJSON('/api/events?' + q.toString())
}

onMounted(async () => {
  try {
    if (route.query.batch_id) batchId.value = Number(route.query.batch_id)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(batchId, async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>事件留痕</h1>
    <p class="muted">
      本批（或全部）已处理事件：结果为写入 / 删除 / 跳过重复。每行保留当时生效的合并策略、合并前后载荷；
      整行替换下清除字段标出原因（显式 null 或缺省），补丁下未给字段单独列出保留清单。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;gap:0.75rem;align-items:flex-end">
      <label>
        批次 ID（可选）
        <input v-model.number="batchId" type="number" style="display:block;margin-top:0.25rem;width:8rem" placeholder="全部" />
      </label>
      <button @click="batchId = null; refresh()">看全部</button>
    </div>
    <div class="panel">
      <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>event_id</th><th>实体键</th><th>op</th><th>seq</th>
            <th>策略</th><th>载荷</th><th>合并前</th><th>合并后</th><th>保留</th><th>清除</th><th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.batch_id }}</td>
            <td class="mono">{{ e.event_id }}</td>
            <td class="mono">{{ e.entity_key }}</td>
            <td class="mono">{{ e.op }}</td>
            <td class="mono">{{ e.seq }}</td>
            <td><span class="tag warn">{{ e.merge_mode || '—' }}</span></td>
            <td class="mono cell-payload" :title="fullJson(e.payload)">
              <span v-if="!payloadParts(e.payload).length" class="muted">—</span>
              <span v-for="(p, i) in payloadParts(e.payload)" :key="i" class="kv">{{ p }}</span>
            </td>
            <td class="mono cell-payload" :title="fullJson(e.before_payload)">
              <span v-if="!payloadParts(e.before_payload).length" class="muted">—</span>
              <span v-for="(p, i) in payloadParts(e.before_payload)" :key="i" class="kv">{{ p }}</span>
            </td>
            <td class="mono cell-payload" :title="fullJson(e.after_payload)">
              <span v-if="!payloadParts(e.after_payload).length" class="muted">—</span>
              <span v-for="(p, i) in payloadParts(e.after_payload)" :key="i" class="kv">{{ p }}</span>
            </td>
            <td class="mono">
              <span v-if="e.preserved_fields.length" class="tag ok">{{ e.preserved_fields.join(', ') }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <span
                v-for="c in e.cleared_fields"
                :key="c.field"
                class="tag danger"
                style="margin:0 0.2rem 0.2rem 0"
              >
                {{ c.field }}（{{ reasonName(c.reason) }}）
              </span>
              <span v-if="!e.cleared_fields.length" class="muted">—</span>
            </td>
            <td><span class="tag">{{ e.result }}</span></td>
          </tr>
          <tr v-if="!rows.length"><td colspan="12" class="muted">暂无留痕。</td></tr>
        </tbody>
      </table>
      </div>
    </div>
  </div>
</template>
