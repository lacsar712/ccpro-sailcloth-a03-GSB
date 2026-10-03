<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'

const lofts = ref([])
const rows = ref([])
const loading = ref(false)
const error = ref('')
const loftFilter = ref('')

const statusLabel = { raw: '原布', dipping: '浸渍中', cured: '已固化' }

const filteredRows = computed(() => {
  if (!loftFilter.value) return rows.value
  return rows.value.filter((r) => r.loftId === Number(loftFilter.value))
})

const grouped = computed(() => {
  const map = new Map()
  for (const row of filteredRows.value) {
    if (!map.has(row.loftId)) {
      map.set(row.loftId, { loftId: row.loftId, loftName: row.loftName, rows: [] })
    }
    map.get(row.loftId).rows.push(row)
  }
  return [...map.values()]
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [l, d] = await Promise.all([
      api.get('/lofts/'),
      api.get('/weight-diff/', loftFilter.value ? { params: { loftId: loftFilter.value } } : undefined),
    ])
    lofts.value = l.data.results || l.data
    rows.value = d.data.results || d.data
  } catch {
    error.value = '克重差对照加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1>克重差对照</h1>
    <p class="sub">
      只读对照：同一帆布间内，各卷与「浸渍中」邻卷的当前克重差。转入浸渍中时差不得超过 40 gsm；
      同间没有浸渍中邻卷的卷不参与比较（显示 —）。
    </p>
    <p v-if="error" class="error">{{ error }}</p>

    <form class="panel row" @submit.prevent>
      <label>帆布间
        <select v-model="loftFilter" @change="load">
          <option value="">全部帆布间</option>
          <option v-for="l in lofts" :key="l.id" :value="l.id">{{ l.name }}</option>
        </select>
      </label>
      <button class="btn secondary" type="button" :disabled="loading" @click="load">刷新对照</button>
    </form>

    <section v-for="group in grouped" :key="group.loftId" class="panel" style="margin-bottom:16px">
      <h2 class="feed-title">{{ group.loftName }}</h2>
      <table>
        <thead>
          <tr>
            <th>卷号</th>
            <th>状态</th>
            <th>克重 gsm</th>
            <th>同间浸渍中邻卷</th>
            <th>当前差 gsm</th>
            <th>判定</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in group.rows" :key="r.rollId">
            <td>{{ r.rollCode }}</td>
            <td><span class="badge" :class="'badge-' + r.status">{{ statusLabel[r.status] || r.status }}</span></td>
            <td>{{ r.fabricWeightGsm }}</td>
            <td>{{ r.dippingNeighbors.length ? r.dippingNeighbors.join('、') : '—' }}</td>
            <td>{{ r.currentDiff ?? '—' }}</td>
            <td>
              <span v-if="r.currentDiff === null" class="hint">无浸渍中邻卷，不比较</span>
              <span v-else-if="r.withinLimit" class="badge badge-cured">差 {{ r.currentDiff }} ≤ {{ r.limit }}，允许</span>
              <span v-else class="badge" style="background:#f6d9d6;color:#8a2620">差 {{ r.currentDiff }} &gt; {{ r.limit }}，超限</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
    <p v-if="!grouped.length && !error" class="hint">暂无布卷数据</p>
  </div>
</template>
