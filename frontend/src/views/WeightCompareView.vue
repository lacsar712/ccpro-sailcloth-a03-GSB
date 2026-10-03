<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'

const lofts = ref([])
const rows = ref([])
const loading = ref(false)
const error = ref('')
const loftFilter = ref('')

const statusLabel = { raw: '原布', dipping: '浸渍中', cured: '已固化' }
const LIMIT = 40

const groups = computed(() => {
  return lofts.value
    .filter((l) => !loftFilter.value || String(l.id) === String(loftFilter.value))
    .map((loft) => ({
      loft,
      rows: rows.value.filter((r) => r.loftId === loft.id),
    }))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = {}
    if (loftFilter.value) params.loftId = loftFilter.value
    const [l, d] = await Promise.all([
      api.get('/lofts/'),
      api.get('/rolls/weight-diffs/', { params }),
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
      只读对照：同一帆布间内，各卷与「浸渍中」邻卷的克重差。转入浸渍中时差不得超过
      {{ LIMIT }} gsm（含 {{ LIMIT }}）；同间没有浸渍中邻卷时不比较。
    </p>
    <p v-if="error" class="error">{{ error }}</p>

    <div class="panel row">
      <label>按帆布间筛选
        <select v-model="loftFilter" @change="load">
          <option value="">全部帆布间</option>
          <option v-for="l in lofts" :key="l.id" :value="l.id">{{ l.name }}</option>
        </select>
      </label>
      <button class="btn secondary" type="button" :disabled="loading" @click="load">刷新</button>
    </div>

    <section v-for="group in groups" :key="group.loft.id" class="panel">
      <h2 class="feed-title">{{ group.loft.name }}</h2>
      <p class="hint" style="margin: 2px 0 12px">{{ group.loft.location || '工位' }}</p>
      <table>
        <thead>
          <tr>
            <th>卷号</th>
            <th>状态</th>
            <th>克重 gsm</th>
            <th>浸渍中邻卷</th>
            <th>当前最大差</th>
            <th>判定</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in group.rows" :key="r.id">
            <td>{{ r.rollCode }}</td>
            <td>
              <span class="badge" :class="'badge-' + r.status">
                {{ statusLabel[r.status] || r.status }}
              </span>
            </td>
            <td>{{ r.fabricWeightGsm }}</td>
            <td>
              <template v-if="r.dippingNeighbors.length">
                <span v-for="n in r.dippingNeighbors" :key="n.rollCode" class="nb-chip">
                  {{ n.rollCode }} {{ n.fabricWeightGsm }}gsm · 差 {{ n.diff }}
                </span>
              </template>
              <span v-else class="hint">无浸渍中邻卷，不比较</span>
            </td>
            <td>{{ r.maxDiff === null ? '—' : r.maxDiff }}</td>
            <td>
              <span v-if="r.status === 'dipping'" class="hint">本身浸渍中</span>
              <span v-else-if="r.withinLimit" class="ok">允许转入（≤ {{ LIMIT }}）</span>
              <span v-else class="error">差过大，转入将被挡住</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
    <p v-if="!groups.length && !error" class="hint">暂无数据</p>
  </div>
</template>

<style scoped>
.nb-chip {
  display: inline-block;
  margin: 2px 6px 2px 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--canvas-deep);
  font-size: 0.84rem;
  white-space: nowrap;
}
</style>
