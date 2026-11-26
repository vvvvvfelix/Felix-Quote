/* ============================ 组件：Token 用量 ============================ */
const UsagePage = {
  setup() {
    const d = ref(null);
    onMounted(async () => { try { d.value = await api.get('/api/ai/usage'); } catch (e) { notify(e.message, 'err'); } });
    return { d, notify };
  },
  template: `
  <div>
    <div class="card" style="background:linear-gradient(120deg,#fff,#F2F9F4);border-color:#D5E9DD">
      <div class="card-b">
        <div style="display:flex;align-items:center;gap:15px">
          <div style="width:46px;height:46px;border-radius:11px;background:#E7F5EC;display:grid;place-items:center;font-size:22px">◔</div>
          <div>
            <div style="font-size:17px;font-weight:800">本地作业 Token 消耗</div>
            <div style="font-size:12px;color:#5A6070;margin-top:3px">
              当前为纯本地模式 — 产品库、文档解析、报价单生成、PDF 导出全部在本机完成，<b>不消耗任何 Token</b>。</div>
          </div>
        </div>
      </div>
    </div>
    <div class="stats" v-if="d">
      <div class="stat k"><div class="lb">累计 Token</div><div class="vl">{{ d.total_tokens }}</div>
        <div class="sf">仅在启用云端增强时产生</div></div>
      <div class="stat g"><div class="lb">任务记录数</div><div class="vl">{{ d.rows.length }}</div>
        <div class="sf">按任务类型统计</div></div>
    </div>
    <div class="card" v-if="d">
      <div class="card-h"><div><h3>任务明细</h3><div class="d">local 模式记录不计入额度</div></div></div>
      <div class="card-b tight">
        <div v-if="!d.rows.length" class="empty"><div class="ic">◔</div>
          <div class="t">暂无云端任务</div><div>本地模式 0 Token，符合预期</div></div>
        <div v-else class="tbl-wrap"><table class="tbl responsive">
          <thead><tr><th>任务</th><th>模式</th><th class="r">次数</th>
            <th class="r">输入字符</th><th class="r">输出字符</th><th class="r">估算 Token</th></tr></thead>
          <tbody><tr v-for="(r,i) in d.rows" :key="i">
            <td data-label="任务">{{ r.task }}</td><td data-label="模式"><span class="tag" :class="r.mode==='cloud'?'warn':'ok'">{{ r.mode }}</span></td>
            <td class="r mono" data-label="次数">{{ r.n }}</td><td class="r mono" data-label="输入字符">{{ r.ic||0 }}</td>
            <td class="r mono" data-label="输出字符">{{ r.oc||0 }}</td>
            <td class="r mono b" data-label="估算 Token">{{ r.tk||0 }}</td>
          </tr></tbody>
        </table></div>
      </div>
    </div>
  </div>`
};
