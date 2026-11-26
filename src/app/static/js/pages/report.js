/* ============================ 组件：报表与打印 ============================ */
const ReportPage = {
  setup() {
    const { ref, reactive, computed, onMounted, nextTick } = Vue;
    const tab = ref('center');            // center | invoice | slips | itinerary
    const types = ref([]);
    const loading = ref(false);

    /* ---------- 报表中心 ---------- */
    const quotes = ref([]), orders = ref([]);
    const pickType = ref(''), pickId = ref(null);
    async function loadTypes() {
      try { types.value = await api.get('/api/report/types'); } catch (e) { notify(e.message, 'err'); }
    }
    async function loadSources() {
      try { quotes.value = await api.get('/api/quotations'); } catch (e) {}
      try { orders.value = await api.get('/api/orders'); } catch (e) {}
    }
    function openPrint(t) {
      if (t === 'invoice') { tab.value = 'invoice'; return; }
      if (t === 'slips') { tab.value = 'slips'; return; }
      if (t === 'itinerary') { tab.value = 'itinerary'; return; }
      if (t === 'quotation') { tab.value = 'quotation'; pickType.value = 'quotation'; return; }
      if (t === 'delivery') { tab.value = 'delivery'; pickType.value = 'delivery'; return; }
    }

    /* ---------- 发票 ---------- */
    const inv = reactive({
      date: new Date().toISOString().slice(0, 10),
      buyer: '', taxno: '', addr: '', seller: '', sellerTax: '', sellerAddr: '',
      remark: '', layout: 'duplex', items: [],
    });
    function addInvItem() { inv.items.push({ name: '', spec: '', qty: 1, price: 0, tax: 13 }); }
    function delInvItem(i) { inv.items.splice(i, 1); }
    const invTotal = computed(() => inv.items.reduce((s, x) => s + (Number(x.qty) * Number(x.price)), 0));
    const invTax = computed(() => inv.items.reduce((s, x) => s + (Number(x.qty) * Number(x.price) * Number(x.tax) / 100), 0));

    /* ---------- 行程单 ---------- */
    const iti = reactive({ who: '', dept: '', from: '', to: '', reason: '', items: [] });
    function addIti() { iti.items.push({ date: '', line: '', vehicle: '', amount: 0, note: '' }); }
    function delIti(i) { iti.items.splice(i, 1); }
    const itiTotal = computed(() => iti.items.reduce((s, x) => s + Number(x.amount || 0), 0));

    /* ---------- 水单报销 ---------- */
    const slips = ref([]), masking = ref(false), activeSlip = ref(null);
    const drag = reactive({ on: false, x0: 0, y0: 0, x1: 0, y1: 0 });
    async function loadSlips() {
      try { slips.value = await api.get('/api/report/slips'); } catch (e) { notify(e.message, 'err'); }
    }
    async function uploadSlips(e) {
      const files = Array.from(e.target.files || []);
      for (const f of files) {
        try { await api.upload('/api/report/slips/upload', f); }
        catch (err) { notify('上传失败 ' + f.name + ': ' + err.message, 'err'); }
      }
      e.target.value = '';
      await loadSlips();
      notify('水单已导入，可在右侧排版打印', 'ok');
    }
    async function delSlip(id) {
      if (!confirm('确认删除该水单？')) return;
      try { await api.del('/api/report/slips/' + id); await loadSlips(); } catch (e) { notify(e.message, 'err'); }
    }
    async function saveMask(s) {
      try { await api.post('/api/report/slips/' + s.id + '/mask', { mask: s.mask }); }
      catch (e) { notify(e.message, 'err'); }
    }
    function startMask(s, ev) {
      if (!masking.value) return;
      activeSlip.value = s;
      const r = ev.currentTarget.getBoundingClientRect();
      const x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
      drag.on = true; drag.x0 = x; drag.y0 = y; drag.x1 = x; drag.y1 = y;
      const move = (e2) => {
        const r2 = ev.currentTarget.getBoundingClientRect();
        drag.x1 = Math.min(1, Math.max(0, (e2.clientX - r2.left) / r2.width));
        drag.y1 = Math.min(1, Math.max(0, (e2.clientY - r2.top) / r2.height));
      };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        drag.on = false;
        const rect = { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1),
                       w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) };
        if (rect.w > 0.02 && rect.h > 0.02) { s.mask.push(rect); saveMask(s); }
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }
    function clearMask(s) { s.mask = []; saveMask(s); }

    /* ---------- 打印 ---------- */
    const printView = ref(null);
    async function doPrint(view) {
      printView.value = view;
      await nextTick();
      window.print();
      setTimeout(() => { printView.value = null; }, 300);
    }

    onMounted(() => { loadTypes(); loadSources(); loadSlips(); });

    return {
      tab, types, loading, quotes, orders, pickType, pickId, openPrint,
      inv, addInvItem, delInvItem, invTotal, invTax,
      iti, addIti, delIti, itiTotal,
      slips, masking, uploadSlips, delSlip, startMask, clearMask, drag,
      printView, doPrint, notify,
    };
  },
  template: `
  <div>
    <style>
      .rp-tabs { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
      .rp-tab { padding:8px 16px; border:1px solid #d4d9e2; border-radius:8px; cursor:pointer; background:#fff; }
      .rp-tab.on { background:#2155cd; color:#fff; border-color:#2155cd; }
      .rp-card { background:#fff; border:1px solid #e6e9f0; border-radius:12px; padding:18px; }
      .rp-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; }
      .rp-type { border:1px solid #e6e9f0; border-radius:10px; padding:14px; cursor:pointer; transition:.15s; }
      .rp-type:hover { border-color:#2155cd; box-shadow:0 4px 14px rgba(33,85,205,.12); }
      .rp-type b { display:block; font-size:15px; margin-bottom:4px; }
      .rp-type .g { color:#888; font-size:12px; }
      .rp-type .d { color:#555; font-size:12px; margin-top:6px; }
      table.fm { width:100%; border-collapse:collapse; font-size:13px; }
      table.fm th, table.fm td { border:1px solid #bbb; padding:5px 7px; }
      table.fm input, table.fm select { width:100%; border:none; outline:none; font-size:13px; }
      .row { display:flex; gap:10px; flex-wrap:wrap; }
      .row > label { flex:1; min-width:160px; font-size:12px; color:#666; }
      .row > label input { width:100%; margin-top:3px; padding:6px 8px; border:1px solid #d4d9e2; border-radius:6px; }
      /* 打印区 */
      .print-root { display:none; }
      @media print {
        #app { visibility:hidden; height:0; overflow:hidden; }
        .print-root { display:block !important; visibility:visible; position:absolute; left:0; top:0; width:210mm; }
        .print-root * { visibility:visible; }
        @page { size:A4; margin:0; }
        .a4-sheet { width:210mm; min-height:297mm; padding:9mm; box-sizing:border-box; page-break-after:always; }
        .a5-slip { width:192mm; height:140mm; box-sizing:border-box; border:1px solid #333; padding:5mm 6mm; margin:0 auto 6mm; }
        .inv-h { text-align:center; font-size:18px; font-weight:700; letter-spacing:4px; margin-bottom:8px; }
        .inv-meta { display:flex; justify-content:space-between; font-size:12px; margin-bottom:6px; }
        .inv-tab th, .inv-tab td { border:1px solid #333; padding:4px; font-size:12px; }
        .inv-foot { display:flex; justify-content:space-between; font-size:12px; margin-top:8px; }
        .slip-grid { display:grid; grid-template-columns:1fr 1fr; gap:4mm; }
        .slip-cell { border:1px dashed #999; padding:2mm; }
        .slip-cell img { width:100%; display:block; }
        .slip-mask { position:absolute; background:rgba(0,0,0,.82); }
        .slip-wrap { position:relative; }
      }
    </style>

    <div class="rp-tabs">
      <div class="rp-tab" :class="{on:tab==='center'}" @click="tab='center'">报表中心</div>
      <div class="rp-tab" :class="{on:tab==='invoice'}" @click="tab='invoice'">发票打印</div>
      <div class="rp-tab" :class="{on:tab==='slips'}" @click="tab='slips'">水单报销</div>
      <div class="rp-tab" :class="{on:tab==='itinerary'}" @click="tab='itinerary'">行程单</div>
    </div>

    <!-- ============ 报表中心 ============ -->
    <div v-if="tab==='center'" class="rp-card">
      <div class="muted small" style="margin-bottom:12px">点击任一类报表进入打印视图。报价单/送货单可直接复用业务数据；发票/行程单/水单为专用排版。</div>
      <div class="rp-grid">
        <div v-for="t in types" :key="t.key" class="rp-type" @click="openPrint(t.key)">
          <b>{{ t.name }}</b>
          <span class="g">来自：{{ t.source }} · {{ t.group }}</span>
          <div class="d">{{ t.desc }}</div>
        </div>
      </div>

      <div v-if="pickType==='quotation'" class="rp-card" style="margin-top:14px">
        <div class="toolbar"><b>选择报价单</b><span class="grow"></span>
          <button class="btn" @click="doPrint('quotation')">打印预览</button></div>
        <select v-model="pickId" style="width:100%;padding:7px;margin-top:8px">
          <option :value="null">— 请选择 —</option>
          <option v-for="q in quotes" :key="q.id" :value="q.id">#{{ q.id }} {{ q.title||q.quote_no }} · {{ q.currency }} {{ q.total_amount }}</option>
        </select>
      </div>
      <div v-if="pickType==='delivery'" class="rp-card" style="margin-top:14px">
        <div class="toolbar"><b>选择销售订单</b><span class="grow"></span>
          <button class="btn" @click="doPrint('delivery')">打印预览</button></div>
        <select v-model="pickId" style="width:100%;padding:7px;margin-top:8px">
          <option :value="null">— 请选择 —</option>
          <option v-for="o in orders" :key="o.id" :value="o.id">#{{ o.id }} {{ o.title||o.order_no||'' }}</option>
        </select>
      </div>
    </div>

    <!-- ============ 发票打印 ============ -->
    <div v-else-if="tab==='invoice'" class="rp-card">
      <div class="toolbar"><b>发票 / 票据打印</b><span class="grow"></span>
        <label class="muted small">排版：
          <select v-model="inv.layout">
            <option value="duplex">A4 双联（上下两张 A5）</option>
            <option value="single">A4 单张</option>
          </select>
        </label>
        <button class="btn" @click="doPrint('invoice')">打印</button>
      </div>
      <div class="row" style="margin-top:12px">
        <label>开票日期<input v-model="inv.date"></label>
        <label>购买方名称<input v-model="inv.buyer"></label>
        <label>纳税人识别号<input v-model="inv.taxno"></label>
        <label>地址 / 电话<input v-model="inv.addr"></label>
        <label>销售方名称<input v-model="inv.seller"></label>
        <label>销售方税号<input v-model="inv.sellerTax"></label>
        <label>销售方地址电话<input v-model="inv.sellerAddr"></label>
      </div>
      <table class="fm" style="margin-top:12px">
        <thead><tr><th>货物或应税劳务名称</th><th>规格</th><th>数量</th><th>单价</th><th>税率%</th><th>金额</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(x,i) in inv.items" :key="i">
            <td><input v-model="x.name"></td><td><input v-model="x.spec"></td>
            <td><input type="number" v-model="x.qty" style="width:60px"></td>
            <td><input type="number" v-model="x.price" style="width:80px"></td>
            <td><input type="number" v-model="x.tax" style="width:50px"></td>
            <td>{{ (Number(x.qty)*Number(x.price)).toFixed(2) }}</td>
            <td><button class="btn danger" @click="delInvItem(i)">×</button></td>
          </tr>
        </tbody>
      </table>
      <button class="btn" style="margin-top:8px" @click="addInvItem">+ 添加明细</button>
      <div style="text-align:right;margin-top:8px">价税合计（不含税）：<b>{{ invTotal.toFixed(2) }}</b> · 税额：<b>{{ invTax.toFixed(2) }}</b></div>
      <div class="muted small" style="margin-top:6px">备注：<input v-model="inv.remark" style="width:60%"></div>
    </div>

    <!-- ============ 水单报销 ============ -->
    <div v-else-if="tab==='slips'" class="rp-card">
      <div class="toolbar"><b>微信 / 支付宝水单报销</b><span class="grow"></span>
        <button class="btn" :class="{on:masking}" @click="masking=!masking">{{ masking?'退出脱敏':'脱敏遮挡' }}</button>
        <button class="btn" @click="doPrint('slips')">排版打印</button>
      </div>
      <div class="muted small" style="margin:8px 0">导入水单截图 → 自动排列到 A4（两列）→ 可选遮挡金额/姓名 → 直接连接打印机。脱敏开启后，在图片上拖拽框选需遮挡区域。</div>
      <input type="file" accept="image/*" multiple @change="uploadSlips" style="margin-bottom:10px">
      <div v-if="!slips.length" class="muted">暂无水单，先导入几张微信/支付宝付款截图。</div>
      <div class="rp-grid">
        <div v-for="s in slips" :key="s.id" class="rp-type">
          <div class="slip-wrap" @mousedown="startMask(s,$event)">
            <img :src="s.url" style="width:100%;display:block;border:1px solid #ddd">
            <div v-for="(m,i) in s.mask" :key="i" class="slip-mask"
                 :style="{left:(m.x*100)+'%',top:(m.y*100)+'%',width:(m.w*100)+'%',height:(m.h*100)+'%'}"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:6px;font-size:12px">
            <span class="muted">{{ s.filename }}</span>
            <span>
              <a href="#" @click.prevent="clearMask(s)" v-if="s.mask.length">清遮挡</a>
              <a href="#" @click.prevent="delSlip(s.id)" style="color:#c0392b;margin-left:8px">删除</a>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 行程单 ============ -->
    <div v-else-if="tab==='itinerary'" class="rp-card">
      <div class="toolbar"><b>差旅行程单</b><span class="grow"></span>
        <button class="btn" @click="doPrint('itinerary')">打印</button></div>
      <div class="row" style="margin-top:12px">
        <label>出差人<input v-model="iti.who"></label>
        <label>部门<input v-model="iti.dept"></label>
        <label>出发地<input v-model="iti.from"></label>
        <label>目的地<input v-model="iti.to"></label>
        <label>事由<input v-model="iti.reason"></label>
      </div>
      <table class="fm" style="margin-top:12px">
        <thead><tr><th>日期</th><th>行程</th><th>交通方式</th><th>金额</th><th>备注</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(x,i) in iti.items" :key="i">
            <td><input v-model="x.date" style="width:90px"></td>
            <td><input v-model="x.line"></td>
            <td><input v-model="x.vehicle" style="width:80px"></td>
            <td><input type="number" v-model="x.amount" style="width:80px"></td>
            <td><input v-model="x.note"></td>
            <td><button class="btn danger" @click="delIti(i)">×</button></td>
          </tr>
        </tbody>
      </table>
      <button class="btn" style="margin-top:8px" @click="addIti">+ 添加行程</button>
      <div style="text-align:right;margin-top:8px">合计：<b>{{ itiTotal.toFixed(2) }}</b></div>
    </div>

    <!-- ============ 打印区（屏幕隐藏，打印时显示） ============ -->
    <div class="print-root">
      <!-- 发票 -->
      <div v-if="printView==='invoice'" v-for="c in (inv.layout==='duplex'?[1,2]:[1])" :key="'inv'+c" class="a4-sheet">
        <div class="a5-slip">
          <div class="inv-h">发 票</div>
          <div class="inv-meta"><span>开票日期：{{ inv.date }}</span><span> No. ________</span></div>
          <div class="inv-meta"><span>购买方：{{ inv.buyer }}</span></div>
          <div class="inv-meta"><span>税号：{{ inv.taxno }}</span><span>电话：{{ inv.addr }}</span></div>
          <table class="inv-tab" style="width:100%;border-collapse:collapse;margin-top:6px">
            <thead><tr><th>名称</th><th>规格</th><th>数量</th><th>单价</th><th>税率</th><th>金额</th></tr></thead>
            <tbody>
              <tr v-for="(x,i) in inv.items" :key="i">
                <td>{{ x.name }}</td><td>{{ x.spec }}</td><td>{{ x.qty }}</td>
                <td>{{ x.price }}</td><td>{{ x.tax }}%</td><td>{{ (Number(x.qty)*Number(x.price)).toFixed(2) }}</td>
              </tr>
              <tr v-if="!inv.items.length"><td colspan="6" style="text-align:center;color:#999">（无明细）</td></tr>
            </tbody>
          </table>
          <div class="inv-foot"><span>价税合计：{{ invTotal.toFixed(2) }}（税额 {{ invTax.toFixed(2) }}）</span><span>第 {{ c }} 联</span></div>
          <div class="inv-foot"><span>销售方：{{ inv.seller }}（{{ inv.sellerTax }}）</span></div>
          <div class="muted small" style="margin-top:6px">备注：{{ inv.remark }}</div>
        </div>
      </div>

      <!-- 水单 -->
      <div v-if="printView==='slips'" class="a4-sheet">
        <div class="inv-h" style="font-size:15px;margin-bottom:6px">付款水单报销单</div>
        <div class="slip-grid">
          <div v-for="s in slips" :key="s.id" class="slip-cell">
            <div class="slip-wrap">
              <img :src="s.url" style="width:100%;display:block">
              <div v-for="(m,i) in s.mask" :key="i" class="slip-mask"
                   :style="{left:(m.x*100)+'%',top:(m.y*100)+'%',width:(m.w*100)+'%',height:(m.h*100)+'%'}"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 行程单 -->
      <div v-if="printView==='itinerary'" class="a4-sheet">
        <div class="inv-h">差 旅 行 程 单</div>
        <div class="inv-meta"><span>出差人：{{ iti.who }}</span><span>部门：{{ iti.dept }}</span></div>
        <div class="inv-meta"><span>行程：{{ iti.from }} → {{ iti.to }}</span><span>事由：{{ iti.reason }}</span></div>
        <table class="inv-tab" style="width:100%;border-collapse:collapse;margin-top:8px">
          <thead><tr><th>日期</th><th>行程</th><th>交通</th><th>金额</th><th>备注</th></tr></thead>
          <tbody>
            <tr v-for="(x,i) in iti.items" :key="i">
              <td>{{ x.date }}</td><td>{{ x.line }}</td><td>{{ x.vehicle }}</td>
              <td>{{ x.amount }}</td><td>{{ x.note }}</td>
            </tr>
            <tr v-if="!iti.items.length"><td colspan="5" style="text-align:center;color:#999">（无记录）</td></tr>
          </tbody>
        </table>
        <div class="inv-foot"><span>合计：{{ itiTotal.toFixed(2) }}</span><span>报销人：__________ 审批：__________</span></div>
      </div>

      <!-- 报价单 / 送货单（占位提示，实际数据由后端拉取渲染） -->
      <div v-if="printView==='quotation' || printView==='delivery'" class="a4-sheet">
        <div class="inv-h">{{ printView==='quotation'?'报 价 单':'送 货 单' }}</div>
        <div class="muted" style="text-align:center;margin-top:30px">请通过「报表中心」选择具体单据后打印；此处为排版容器。</div>
      </div>
    </div>
  </div>
  `,
};
