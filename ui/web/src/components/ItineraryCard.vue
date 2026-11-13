<script setup>
defineProps({
  option: { type: Object, required: true },
})

function legTime(leg, key) {
  const value = leg[key]
  if (!value) return '-'
  const clock = String(value).slice(0, 5)
  if (!leg.day_offset) return clock
  return leg.day_offset === 1 ? `次日 ${clock}` : `第${leg.day_offset + 1}日 ${clock}`
}
</script>

<template>
  <div class="itinerary">
    <div class="head">{{ option.title }}</div>
    <div class="body">
      <div class="section return" v-if="option.return_time">
        <span class="label">预计返回：</span>{{ option.return_time }}
      </div>
      <div v-if="option.reason" class="section">
        <span class="label">推荐理由：</span>{{ option.reason }}
      </div>
      <div v-if="option.weather_advice" class="section">
        <span class="label">天气建议：</span>{{ option.weather_advice }}
      </div>

      <div class="section" v-if="(option.legs || []).filter((l) => l.arrive_time).length">
        <div class="label">行程时间轴</div>
        <table>
          <thead>
            <tr><th>停留</th><th>到达</th><th>离开</th><th>时长(分)</th></tr>
          </thead>
          <tbody>
            <tr v-for="(l, i) in option.legs" :key="i" v-show="l.arrive_time">
              <td>{{ l.name }}</td>
              <td>{{ legTime(l, 'arrive_time') }}</td>
              <td>{{ legTime(l, 'leave_time') }}</td>
              <td>{{ l.duration_minutes ?? '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="section" v-if="(option.drives || []).length">
        <div class="label">驾车路线</div>
        <table>
          <thead>
            <tr><th>起</th><th>止</th><th>距离(km)</th><th>耗时(分)</th><th>过路费(元)</th></tr>
          </thead>
          <tbody>
            <tr v-for="(d, i) in option.drives" :key="i">
              <td>{{ d.origin }}</td>
              <td>{{ d.destination }}</td>
              <td>{{ d.distance_km }}</td>
              <td>{{ d.duration_minutes }}</td>
              <td>{{ d.toll_fee }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="section" v-if="option.budget">
        <div class="label">
          预算{{ option.budget.is_lower_bound ? '（已知费用下限）' : '' }}
        </div>
        <table>
          <thead><tr><th>项目</th><th>金额(元)</th></tr></thead>
          <tbody>
            <tr v-for="(item, i) in option.budget.items" :key="i">
              <td>{{ item.name }}</td>
              <td>{{ item.amount }}</td>
            </tr>
            <tr>
              <td><b>{{ option.budget.is_lower_bound ? '已知合计（下限）' : '合计' }}</b></td>
              <td><b>{{ option.budget.total_cost }}</b></td>
            </tr>
          </tbody>
        </table>
        <div v-if="option.budget.is_lower_bound" class="bound-note">
          另有 {{ option.budget.unknown_ticket_count }} 个景点门票未知，未按免费计入，实际费用可能更高。
        </div>
      </div>

      <div class="section" v-if="(option.risks || []).length">
        <div class="label">风险提醒</div>
        <span v-for="(r, i) in option.risks" :key="i" class="chip">{{ r }}</span>
      </div>

      <div class="section" v-if="option.rain_alternative">
        <div class="label">雨天备选</div>{{ option.rain_alternative }}
      </div>

      <div class="section" v-if="(option.attractions || []).length">
        <div class="label">景点介绍</div>
        <div v-for="(a, i) in option.attractions" :key="i" class="attr">
          <b>{{ a.name }}</b>（{{ a.district }}）
          <div class="intro">{{ a.introduction }}</div>
          <span class="chip info">
            门票：{{ a.ticket_price != null ? a.ticket_price + ' 元' : (a.ticket_note || '暂无可靠数据') }}
          </span>
        </div>
      </div>

      <div class="section" v-if="(option.citations || []).length">
        <div class="label">数据来源与更新时间</div>
        <div v-for="(c, i) in option.citations" :key="i" class="cite">{{ c }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.attr {
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
}
.attr:last-child {
  border-bottom: none;
}
.intro {
  font-size: 13px;
  color: var(--text-sub);
  margin: 4px 0;
}
.cite {
  font-size: 12px;
  color: var(--text-sub);
}
.bound-note {
  font-size: 12px;
  color: #c2680b;
  margin-top: 4px;
}
.return {
  font-weight: 600;
}
</style>