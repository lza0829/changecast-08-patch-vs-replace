<script setup>
import { onMounted, ref, watch } from 'vue'
import { getJSON, postJSON } from '../api'

const streams = ref([])
const batches = ref([])
const compare = ref(null)
const streamId = ref(null)
const note = ref('')
const last = ref(null)
const busy = ref(false)
const err = ref('')

const MODE_NAMES = { patch: '字段级补丁', replace: '整行替换' }

async function refresh() {
  streams.value = await getJSON('/api/streams')
  batches.value = await getJSON('/api/batches?limit=20')
  if (streamId.value == null && streams.value.length) streamId.value = streams.value[0].id
}

async function loadCompare() {
  if (streamId.value == null) return
  compare.value = await getJSON(`/api/streams/${streamId.value}/merge-compare`)
}

async function run() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/project', { stream_id: Number(streamId.value), note: note.value || null })
    await refresh()
    await loadCompare()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function fmt(v, present = true) {
  if (!present) return '（未出现）'
  if (v === null) return 'null'
  return JSON.stringify(v)
}

onMounted(async () => {
  try {
    await refresh()
    await loadCompare()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(streamId, async () => {
  try {
    compare.value = null
    await loadCompare()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>
<template>
  <div class="page">
    <h1>投影台</h1>
    <p class="muted">
      选一条投影流，读取 CDC 夹具并按 seq 应用到投影表。upsert 合并策略随流配置：补丁保留未给字段，整行替换清除未给字段。
      批次进行中不可改策略，切换只影响之后新开批次。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">发起投影</h3>
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
        <label>
          投影流
          <select v-model.number="streamId" style="display:block;min-width:18rem;margin-top:0.25rem">
            <option v-for="s in streams" :key="s.id" :value="s.id">
              {{ s.title }} · {{ MODE_NAMES[s.upsert_mode] || s.upsert_mode }}
            </option>
          </select>
        </label>
        <label>
          备注（可选）
          <input v-model="note" style="display:block;width:16rem;margin-top:0.25rem" placeholder="本趟说明" />
        </label>
        <button :disabled="busy || !streamId" @click="run">{{ busy ? '投影中…' : '应用变更流' }}</button>
      </div>
      <p class="muted" style="font-size:0.85rem;margin-bottom:0">
        当前流策略：
        <span class="tag" v-if="streams.length && streamId">
          {{ MODE_NAMES[streams.find((s) => s.id === streamId)?.upsert_mode] }}
        </span>
        · 改策略请到 <router-link to="/streams">投影流</router-link>
      </p>
    </div>

    <div class="panel" v-if="last">
      <h3 style="margin-top:0">最近一趟</h3>
      <p>
        批次 <span class="mono">{{ last.batch_no }}</span> · {{ last.stream_title }} ·
        <span class="tag ok">{{ last.status }}</span> ·
        <span class="tag warn">{{ last.upsert_mode_label }}</span>
      </p>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ last.source_events }}</div><div class="stat-label">源事件</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_upsert }}</div><div class="stat-label">写入</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_delete }}</div><div class="stat-label">删除</div></div>
        <div class="stat"><div class="stat-num">{{ last.skipped_duplicate }}</div><div class="stat-label">跳过重复</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_alive }}</div><div class="stat-label">存活</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_deleted }}</div><div class="stat-label">已删</div></div>
      </div>
      <p style="margin-top:0.75rem">
        <router-link :to="{ path: '/projections', query: { stream_id: last.stream_id } }">查看投影快照 →</router-link>
        ·
        <router-link :to="{ path: '/events', query: { batch_id: last.id } }">事件留痕 →</router-link>
      </p>
    </div>

    <div class="panel" v-if="compare">
      <h3 style="margin-top:0">两种策略同批终态对照</h3>
      <p class="muted">
        以同一份当前快照为起点，分别按两种策略回放本流夹具（只读预览，不写库）。可检验两种语义混用时终态差异，
        以及同键连续补丁与一次整行替换是否等价。
      </p>
      <div style="display:flex;flex-wrap:wrap;gap:1rem;margin-bottom:0.75rem">
        <div class="stat" style="flex:1">
          <div class="stat-label">补丁 · 写入 / 存活 / max seq</div>
          <div class="stat-num" style="font-size:1.05rem">
            {{ compare.modes.patch.counts.applied_upsert }} / {{ compare.modes.patch.counts.projected_alive }} /
            {{ compare.modes.patch.counts.max_seq }}
          </div>
          <div class="muted" style="font-size:0.8rem">{{ compare.modes.patch.label }}</div>
        </div>
        <div class="stat" style="flex:1">
          <div class="stat-label">整行替换 · 写入 / 存活 / max seq</div>
          <div class="stat-num" style="font-size:1.05rem">
            {{ compare.modes.replace.counts.applied_upsert }} / {{ compare.modes.replace.counts.projected_alive }} /
            {{ compare.modes.replace.counts.max_seq }}
          </div>
          <div class="muted" style="font-size:0.8rem">{{ compare.modes.replace.label }}</div>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>实体键</th><th>补丁策略终态</th><th>整行替换终态</th><th>差异字段</th><th>seq</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in compare.entities" :key="e.entity_key">
            <td class="mono">{{ e.entity_key }}</td>
            <td class="mono" :class="e.diverges ? '' : 'muted'">
              {{ e.patch.deleted ? '已删' : JSON.stringify(e.patch.payload) }}
            </td>
            <td class="mono" :class="e.diverges ? '' : 'muted'">
              {{ e.replace.deleted ? '已删' : JSON.stringify(e.replace.payload) }}
            </td>
            <td>
              <span v-if="!e.diverges" class="tag">一致</span>
              <span v-else v-for="d in e.field_diffs" :key="d.field" class="tag warn" style="margin:0 0.2rem 0.2rem 0">
                {{ d.field }}：补丁 {{ fmt(d.patch_value, d.patch_present) }} / 替换
                {{ fmt(d.replace_value, d.replace_present) }}
              </span>
            </td>
            <td class="mono">{{ e.patch.applied_seq }} / {{ e.replace.applied_seq }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">历史批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>策略</th><th>状态</th>
            <th>源事件</th><th>写入</th><th>删除</th><th>存活</th><th>已删</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td><span class="tag warn">{{ MODE_NAMES[b.upsert_mode] || b.upsert_mode }}</span></td>
            <td><span class="tag ok">{{ b.status }}</span></td>
            <td>{{ b.source_events }}</td>
            <td>{{ b.applied_upsert }}</td>
            <td>{{ b.applied_delete }}</td>
            <td>{{ b.projected_alive }}</td>
            <td>{{ b.projected_deleted }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="10" class="muted">还没有批次，先跑一趟。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
