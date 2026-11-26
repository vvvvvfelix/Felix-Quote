/* ============================ 组件：电子报销（M071） ============================
   无纸化电子报销：我的报销单 / 新建向导（边填边查重）/ 详情（影像+重复标记）/
   财务复核台 / A4 打印预览（含签名栏）。

   设计要点：
   1. 所有枚举（费用类型/票据类型/单据状态/重复状态）一律从 /api/expense/types 取，
      本文件不硬编码任何枚举的中文 —— 否则加一个费用类型要改前后端两处。
   2. 票据影像复用 M109 的 slips 存储（data/slips + report_slips），
      图片统一走 /api/report/slips/{id}/file，不另起一套附件机制。
   3. 打印是纯前端 @media print 零依赖方案：后端只给数据（含金额大写），
      A4 排版与签名栏全部由 CSS 完成（样式见 index.html 的 .exp-print 段）。
   权限：expense.view 可见入口；expense.edit 起草编辑；expense.finance 复核/打印；
        expense.pay 支付登记。
============================================================================ */
const ExpensePage = {
  setup() {
    const me = ref(null);
    const tab = ref('mine');              // mine 我的 / new 新建 / detail 详情 / review 复核 / print 打印

    /* 枚举（唯一中文来源） */
    const types = ref({ categories: [], ticket_types: [], statuses: [], dup_statuses: [], approval_tiers: [] });
    const catLabel = v => labelOf(types.value.categories, v);
    const ttLabel = v => labelOf(types.value.ticket_types, v);
    const stLabel = v => labelOf(types.value.statuses, v);
    const dupLabel = v => labelOf(types.value.dup_statuses, v);

    const reports = ref([]);
    const detail = ref(null);
    const dups = ref([]);
    const dupFilter = ref('suspected');
    const printData = ref(null);
    const loading = ref(false);

    /* 新建向导：报销单草稿 + 明细行 */
    const draft = ref(null);
    const itemForm = reactive({
      category: 'travel', occur_date: '', amount: 0, ticket_type: 'none',
      invoice_code: '', invoice_no: '', trans_no: '', merchant: '',
      title: '', remark: '', slip_ids: []
    });
    const itemMsg = ref('');               // 边填边查重提示
    const itemErr = ref('');               // 单据内重复等硬拦截错误
    const slipFiles = ref([]);             // 本次待上传的影像
    const submitting = ref(false);

    const canEdit = computed(() => has('expense.edit'));
    const canFinance = computed(() => has('expense.finance'));
    const canPay = computed(() => has('expense.pay'));

    function has(p) {
      const ps = (me.value && me.value.permissions) || [];
      return ps.includes('*') || ps.includes(p);
    }
    function labelOf(list, v) {
      const f = (list || []).find(x => x.value === v);
      return f ? f.label : (v || '');
    }
    const money = v => (Number(v || 0)).toFixed(2);
    const dateShort = s => (s || '').slice(0, 10);

    /* ---------- 数据加载 ---------- */
    async function loadMe() {
      try { me.value = await api.get('/api/org/me'); } catch (e) { me.value = null; }
    }
    async function loadTypes() {
      try { types.value = await api.get('/api/expense/types'); } catch (e) {}
    }
    async function loadReports() {
      loading.value = true;
      try { reports.value = await api.get('/api/expense/reports'); }
      catch (e) { notify(e.message || '加载报销单失败', 'err'); }
      loading.value = false;
    }
    async function loadDups() {
      loading.value = true;
      try { dups.value = await api.get('/api/expense/dups?status=' + dupFilter.value); }
      catch (e) { notify(e.message || '加载复核清单失败', 'err'); }
      loading.value = false;
    }

    async function openDetail(id) {
      loading.value = true;
      try {
        detail.value = await api.get('/api/expense/reports/' + id);
        tab.value = 'detail';
      } catch (e) { notify(e.message || '加载详情失败', 'err'); }
      loading.value = false;
    }

    /* ---------- 新建 ---------- */
    async function createDraft() {
      try {
        draft.value = await api.post('/api/expense/reports', { reason: '' });
        itemForm.slip_ids = [];
        slipFiles.value = [];
        itemMsg.value = ''; itemErr.value = '';
        tab.value = 'new';
      } catch (e) { notify(e.message || '创建报销单失败', 'err'); }
    }

    async function pickSlips(ev) {
      const fs = Array.from(ev.target.files || []);
      if (!fs.length) return;
      const fd = new FormData();
      fs.forEach(f => fd.append('files', f));
      const tk = localStorage.getItem('wv_token');
      try {
        const r = await fetch('/api/expense/slips/upload', {
          method: 'POST', headers: tk ? { Authorization: 'Bearer ' + tk } : {}, body: fd
        });
        if (!r.ok) throw new Error('上传失败 ' + r.status);
        const up = await r.json();
        itemForm.slip_ids = itemForm.slip_ids.concat(up.map(x => x.id));
        slipFiles.value = slipFiles.value.concat(up);
      } catch (e) { notify(e.message || '影像上传失败', 'err'); }
      ev.target.value = '';
    }

    /* 边填边查重：后端返回 dup_of_info 就立刻红字提示，不打断填写 */
    async function addItem() {
      itemErr.value = ''; itemMsg.value = '';
      if (!draft.value) return;
      const body = {
        category: itemForm.category, occur_date: itemForm.occur_date || null,
        amount: Number(itemForm.amount || 0), ticket_type: itemForm.ticket_type,
        invoice_code: itemForm.invoice_code || null,
        invoice_no: itemForm.invoice_no || null,
        trans_no: itemForm.trans_no || null,
        merchant: itemForm.merchant || null, title: itemForm.title || null,
        slip_ids: itemForm.slip_ids, remark: itemForm.remark || null
      };
      try {
        const saved = await api.post('/api/expense/reports/' + draft.value.id + '/items', body);
        if (saved.dup_of_info) {
          const d = saved.dup_of_info;
          itemMsg.value = '该票据疑似重复：已出现在 ' + d.report_code + '（' + d.applicant_name +
            '，' + dateShort(d.occur_date) + '，' + money(d.amount) + ' 元），请核实后提交';
        }
        // 重置为空白行，保留日期便于连续录入同一天的票
        Object.assign(itemForm, {
          category: itemForm.category, occur_date: itemForm.occur_date, amount: 0,
          ticket_type: 'none', invoice_code: '', invoice_no: '', trans_no: '',
          merchant: '', title: '', remark: '', slip_ids: []
        });
        slipFiles.value = [];
        const d2 = await api.get('/api/expense/reports/' + draft.value.id);
        draft.value = d2;
      } catch (e) { itemErr.value = e.message || '保存明细失败'; }
    }

    async function removeItem(id) {
      try {
        await api.del('/api/expense/items/' + id);
        draft.value = await api.get('/api/expense/reports/' + draft.value.id);
      } catch (e) { notify(e.message || '删除失败', 'err'); }
    }

    async function submitReport() {
      if (!draft.value) return;
      submitting.value = true;
      try {
        await api.post('/api/expense/reports/' + draft.value.id + '/submit', {});
        notify('已提交审批', 'ok');
        draft.value = null;
        await loadReports();
        tab.value = 'mine';
      } catch (e) { notify(e.message || '提交失败', 'err'); }
      submitting.value = false;
    }

    /* 当前金额会走到哪一档（提示用，取自后端枚举） */
    const tierHint = computed(() => {
      const amt = Number((draft.value && draft.value.total_amount) || 0);
      const t = (types.value.approval_tiers || []).find(x => amt >= x.min_amount);
      return t ? (t.label + ' 元：' + t.flow) : '';
    });

    /* ---------- 财务复核 ---------- */
    async function resolveDup(itemId, action) {
      try {
        await api.post('/api/expense/items/' + itemId + '/dup', { action });
        notify(action === 'confirm' ? '已标记为核实通过' : '已驳回该票据', 'ok');
        await loadDups();
      } catch (e) { notify(e.message || '操作失败', 'err'); }
    }

    /* ---------- 打印 ---------- */
    async function openPrint(id) {
      loading.value = true;
      try {
        printData.value = await api.get('/api/expense/reports/' + id + '/print-data');
        tab.value = 'print';
      } catch (e) { notify(e.message || '打印数据加载失败', 'err'); }
      loading.value = false;
    }
    async function doPrint() {
      if (!printData.value) return;
      // 先留痕再打印：万一打印中途取消，次数也已经记上（防重复打印报账）
      try {
        await api.post('/api/expense/reports/' + printData.value.id + '/print', {});
      } catch (e) {}
      window.print();
    }

    async function payReport(id) {
      try {
        await api.post('/api/expense/reports/' + id + '/pay', {});
        notify('已登记支付', 'ok');
        await loadReports();
        if (detail.value && detail.value.id === id) {
          detail.value = await api.get('/api/expense/reports/' + id);
        }
      } catch (e) { notify(e.message || '支付登记失败', 'err'); }
    }

    onMounted(async () => {
      await loadMe();
      await loadTypes();
      await loadReports();
    });

    return {
      me, tab, types, catLabel, ttLabel, stLabel, dupLabel,
      reports, detail, dups, dupFilter, printData, loading,
      draft, itemForm, itemMsg, itemErr, slipFiles, submitting, tierHint,
      canEdit, canFinance, canPay, money, dateShort,
      loadReports, loadDups, openDetail, createDraft, pickSlips, addItem,
      removeItem, submitReport, resolveDup, openPrint, doPrint, payReport
    };
  },

  template: /* html */`
  <div class="page">
    <div class="tabs">
      <div class="tab" :class="{on:tab==='mine'}" @click="tab='mine'">我的报销单</div>
      <div class="tab" :class="{on:tab==='new'}" @click="draft?tab='new':createDraft()" v-if="canEdit">新建报销</div>
      <div class="tab" :class="{on:tab==='detail'}" v-if="tab==='detail'">单据详情</div>
      <div class="tab" :class="{on:tab==='review'}" @click="tab='review';loadDups()" v-if="canFinance">财务复核台</div>
      <div class="tab" :class="{on:tab==='print'}" v-if="tab==='print'">打印预览</div>
    </div>

    <!-- ================= 我的报销单 ================= -->
    <div v-if="tab==='mine'">
      <div class="row-end" style="margin-bottom:10px">
        <button class="btn primary" @click="createDraft" v-if="canEdit">+ 新建报销单</button>
      </div>
      <div class="card">
        <table class="tbl responsive">
          <thead><tr>
            <th>单号</th><th>报销期间</th><th>金额</th><th>状态</th>
            <th>明细</th><th>疑似重复</th><th>打印次数</th><th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in reports" :key="r.id">
              <td data-label="单号"><b>{{ r.code }}</b></td>
              <td data-label="报销期间">{{ dateShort(r.period_from) }} ~ {{ dateShort(r.period_to) }}</td>
              <td data-label="金额">{{ money(r.total_amount) }}</td>
              <td data-label="状态"><span class="badge" :class="'st-'+r.status">{{ stLabel(r.status) }}</span></td>
              <td data-label="明细">{{ r.item_count }}</td>
              <td data-label="疑似重复">
                <span v-if="r.dup_count>0" class="badge dup">⚠ {{ r.dup_count }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td data-label="打印次数">{{ r.print_count }}</td>
              <td class="nowrap" data-label="操作">
                <button class="btn sm" @click="openDetail(r.id)">详情</button>
                <button class="btn sm" v-if="canPay && r.status==='approved'" @click="payReport(r.id)">支付</button>
              </td>
            </tr>
            <tr v-if="!reports.length"><td colspan="8" class="muted center" data-label="">暂无报销单</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ================= 新建向导（边填边查重） ================= -->
    <div v-if="tab==='new' && draft">
      <div class="card">
        <div class="row-between">
          <div><b>{{ draft.code }}</b> <span class="badge">{{ stLabel(draft.status) }}</span></div>
          <div class="muted">合计：<b class="money">{{ money(draft.total_amount) }}</b> 元</div>
        </div>
        <div class="muted sm" v-if="tierHint">预计审批流向：{{ tierHint }}</div>

        <div class="grid-2" style="margin-top:12px">
          <div>
            <label class="lbl">费用类型</label>
            <select class="inp" v-model="itemForm.category">
              <option v-for="c in types.categories" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </div>
          <div>
            <label class="lbl">票据类型</label>
            <select class="inp" v-model="itemForm.ticket_type">
              <option v-for="t in types.ticket_types" :key="t.value" :value="t.value">{{ t.label }}</option>
            </select>
          </div>
          <div>
            <label class="lbl">发生日期</label>
            <input class="inp" type="date" v-model="itemForm.occur_date"/>
          </div>
          <div>
            <label class="lbl">金额</label>
            <input class="inp" type="number" step="0.01" v-model.number="itemForm.amount"/>
          </div>
          <div v-if="itemForm.ticket_type==='vat_special'||itemForm.ticket_type==='vat_normal'">
            <label class="lbl">发票代码</label>
            <input class="inp" v-model="itemForm.invoice_code" placeholder="12 位"/>
          </div>
          <div v-if="itemForm.ticket_type==='vat_special'||itemForm.ticket_type==='vat_normal'">
            <label class="lbl">发票号码</label>
            <input class="inp" v-model="itemForm.invoice_no" placeholder="8 位"/>
          </div>
          <div v-if="itemForm.ticket_type==='wechat'||itemForm.ticket_type==='alipay'">
            <label class="lbl">交易单号</label>
            <input class="inp" v-model="itemForm.trans_no"/>
          </div>
          <div>
            <label class="lbl">商家 / 抬头</label>
            <input class="inp" v-model="itemForm.merchant"/>
          </div>
        </div>

        <div class="row" style="margin-top:10px">
          <label class="lbl">票据影像（可多选，复用水单归档）</label>
          <input class="inp" type="file" multiple accept="image/*" @change="pickSlips"/>
          <div class="thumbs">
            <a v-for="s in slipFiles" :key="s.id" :href="s.url" target="_blank" class="thumb">
              <img :src="s.url" alt=""/>
            </a>
          </div>
        </div>

        <div class="msg err" v-if="itemErr">{{ itemErr }}</div>
        <div class="msg warn" v-if="itemMsg">⚠ {{ itemMsg }}</div>

        <div class="row-end" style="margin-top:10px">
          <button class="btn" @click="addItem">+ 添加这条明细</button>
        </div>

        <table class="tbl responsive" style="margin-top:14px" v-if="draft.items && draft.items.length">
          <thead><tr>
            <th>类型</th><th>票据</th><th>日期</th><th>金额</th><th>商家</th>
            <th>影像</th><th>重复状态</th><th></th>
          </tr></thead>
          <tbody>
            <tr v-for="it in draft.items" :key="it.id" :class="{'row-dup':it.dup_status==='suspected'}">
              <td data-label="类型">{{ catLabel(it.category) }}</td>
              <td data-label="票据">{{ ttLabel(it.ticket_type) }}</td>
              <td data-label="日期">{{ dateShort(it.occur_date) }}</td>
              <td data-label="金额">{{ money(it.amount) }}</td>
              <td data-label="商家">{{ it.merchant }}</td>
              <td data-label="影像">
                <a v-for="sid in it.slip_ids" :key="sid"
                   :href="'/api/report/slips/'+sid+'/file'" target="_blank" class="thumb-sm">🖼</a>
              </td>
              <td data-label="重复状态">
                <span class="badge dup" v-if="it.dup_status==='suspected'"
                      :title="it.dup_of_info ? (it.dup_of_info.report_code+' / '+it.dup_of_info.applicant_name) : ''">
                  {{ dupLabel(it.dup_status) }}
                </span>
                <span class="badge ok" v-else-if="it.dup_status==='confirmed_ok'">{{ dupLabel(it.dup_status) }}</span>
                <span class="muted" v-else>{{ dupLabel(it.dup_status) }}</span>
              </td>
              <td data-label="操作"><button class="btn sm danger" @click="removeItem(it.id)">删除</button></td>
            </tr>
          </tbody>
        </table>

        <div class="row-end" style="margin-top:14px">
          <button class="btn primary" :disabled="submitting || !draft.items || !draft.items.length"
                  @click="submitReport">提交审批</button>
        </div>
      </div>
    </div>

    <!-- ================= 详情 ================= -->
    <div v-if="tab==='detail' && detail">
      <div class="card">
        <div class="row-between">
          <div><b>{{ detail.code }}</b> <span class="badge" :class="'st-'+detail.status">{{ stLabel(detail.status) }}</span></div>
          <div class="muted">合计：<b class="money">{{ money(detail.total_amount) }}</b> 元</div>
        </div>
        <div class="muted sm">申请人：{{ detail.applicant_name }} · 组织：{{ detail.org_name || '—' }} · 期间：{{ dateShort(detail.period_from) }} ~ {{ dateShort(detail.period_to) }}</div>
        <div class="muted sm" v-if="detail.reason">事由：{{ detail.reason }}</div>

        <table class="tbl responsive" style="margin-top:12px">
          <thead><tr>
            <th>类型</th><th>票据</th><th>日期</th><th>金额</th><th>商家</th>
            <th>发票/交易号</th><th>影像</th><th>重复状态</th>
          </tr></thead>
          <tbody>
            <tr v-for="it in detail.items" :key="it.id" :class="{'row-dup':it.dup_status==='suspected'}">
              <td data-label="类型">{{ catLabel(it.category) }}</td>
              <td data-label="票据">{{ ttLabel(it.ticket_type) }}</td>
              <td data-label="日期">{{ dateShort(it.occur_date) }}</td>
              <td data-label="金额">{{ money(it.amount) }}</td>
              <td data-label="商家">{{ it.merchant }}</td>
              <td class="sm" data-label="发票/交易号">{{ it.invoice_code || '' }}{{ it.invoice_no || '' }}{{ it.trans_no || '' }}</td>
              <td data-label="影像">
                <a v-for="sid in it.slip_ids" :key="sid"
                   :href="'/api/report/slips/'+sid+'/file'" target="_blank" class="thumb-sm">🖼</a>
              </td>
              <td data-label="重复状态">
                <span class="badge dup" v-if="it.dup_status==='suspected'">
                  {{ dupLabel(it.dup_status) }}
                  <span class="sm" v-if="it.dup_of_info">
                    （{{ it.dup_of_info.report_code }} / {{ it.dup_of_info.applicant_name }} / {{ money(it.dup_of_info.amount) }}）
                  </span>
                </span>
                <span class="badge ok" v-else-if="it.dup_status==='confirmed_ok'">{{ dupLabel(it.dup_status) }}</span>
                <span class="muted" v-else>{{ dupLabel(it.dup_status) }}</span>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="row-end" style="margin-top:12px">
          <button class="btn" v-if="canPay && detail.status==='approved'" @click="payReport(detail.id)">登记支付</button>
          <button class="btn primary" v-if="detail.status==='approved'||detail.status==='paid'"
                  @click="openPrint(detail.id)">打印报销单</button>
          <button class="btn" @click="tab='mine';loadReports()">返回</button>
        </div>
      </div>
    </div>

    <!-- ================= 财务复核台 ================= -->
    <div v-if="tab==='review'">
      <div class="card">
        <div class="row-between" style="margin-bottom:10px">
          <div><b>票据查重复核</b></div>
          <select class="inp" style="width:180px" v-model="dupFilter" @change="loadDups">
            <option value="suspected">待核实（疑似重复）</option>
            <option value="confirmed_ok">已核实通过</option>
          </select>
        </div>
        <table class="tbl responsive">
          <thead><tr>
            <th>所在报销单</th><th>报销人</th><th>票据</th><th>日期</th><th>金额</th>
            <th>重复来源</th><th>状态</th><th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="d in dups" :key="d.id">
              <td data-label="所在报销单">{{ d.report_code }}</td>
              <td data-label="报销人">{{ d.applicant_name }}</td>
              <td data-label="票据">{{ ttLabel(d.ticket_type) }}</td>
              <td data-label="日期">{{ dateShort(d.occur_date) }}</td>
              <td data-label="金额">{{ money(d.amount) }}</td>
              <td data-label="重复来源">
                <span v-if="d.dup_of_info">
                  {{ d.dup_of_info.report_code }} / {{ d.dup_of_info.applicant_name }} /
                  {{ dateShort(d.dup_of_info.occur_date) }} / {{ money(d.dup_of_info.amount) }} 元
                </span>
                <span v-else class="muted">—</span>
              </td>
              <td data-label="状态"><span class="badge" :class="d.dup_status==='suspected'?'dup':'ok'">{{ dupLabel(d.dup_status) }}</span></td>
              <td class="nowrap" data-label="操作" v-if="d.dup_status==='suspected'">
                <button class="btn sm" @click="resolveDup(d.id,'confirm')">核实通过</button>
                <button class="btn sm danger" @click="resolveDup(d.id,'reject')">驳回</button>
              </td>
              <td data-label="操作" v-else class="muted">已处理</td>
            </tr>
            <tr v-if="!dups.length"><td colspan="8" class="muted center" data-label="">没有需要复核的票据</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ================= 打印预览（A4 + 签名栏） ================= -->
    <div v-if="tab==='print' && printData">
      <div class="row-end no-print" style="margin-bottom:10px">
        <button class="btn primary" @click="doPrint">🖨 打印（留痕）</button>
        <button class="btn" @click="tab='detail'">返回</button>
      </div>

      <div class="exp-print">
        <div class="ep-head">
          <div class="ep-co">
            <div class="ep-co-name">{{ printData.company.name_cn }}</div>
            <div class="ep-co-sub">{{ printData.company.name_en }}</div>
          </div>
          <div class="ep-title">费用报销单</div>
        </div>

        <table class="ep-meta">
          <tr>
            <td>单号：<b>{{ printData.code }}</b></td>
            <td>申请人：{{ printData.applicant_name }}</td>
            <td>部门：{{ printData.org_name || '—' }}</td>
            <td>报销期间：{{ dateShort(printData.period_from) }} ~ {{ dateShort(printData.period_to) }}</td>
          </tr>
        </table>
        <div class="ep-amount">
          金额合计（小写）：<b>{{ money(printData.total_amount) }}</b> 元
          &nbsp;&nbsp;（大写）：<b>{{ printData.amount_upper }}</b>
        </div>

        <table class="ep-items">
          <thead><tr>
            <th style="width:36px">#</th><th>费用类型</th><th>票据类型</th>
            <th>发生日期</th><th>商家</th><th>发票/交易号</th><th style="width:90px">金额</th>
          </tr></thead>
          <tbody>
            <tr v-for="(it,i) in printData.items" :key="it.id">
              <td>{{ i+1 }}</td>
              <td>{{ catLabel(it.category) }}</td>
              <td>{{ ttLabel(it.ticket_type) }}</td>
              <td>{{ dateShort(it.occur_date) }}</td>
              <td>{{ it.merchant }}</td>
              <td>{{ it.invoice_code || '' }}{{ it.invoice_no || '' }}{{ it.trans_no || '' }}</td>
              <td class="r">{{ money(it.amount) }}</td>
            </tr>
            <tr v-if="!printData.items.length"><td colspan="7" class="center">无明细</td></tr>
          </tbody>
          <tfoot>
            <tr><td colspan="6" class="r"><b>合计</b></td><td class="r"><b>{{ money(printData.total_amount) }}</b></td></tr>
          </tfoot>
        </table>

        <!-- 票据影像附件页 -->
        <div class="ep-attach" v-if="printData.items.some(i=>i.slips.length)">
          <div class="ep-sec-title">票据影像附件</div>
          <div class="ep-slips">
            <div class="ep-slip" v-for="it in printData.items" :key="it.id">
              <div class="ep-slip-item" v-for="s in it.slips" :key="s.id">
                <img :src="s.url" alt="票据影像"/>
                <div class="ep-slip-cap">{{ catLabel(it.category) }} · {{ money(it.amount) }} 元</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 签名确认栏 -->
        <table class="ep-sign">
          <tr>
            <td>报销人签字</td><td>部门主管</td><td>财务审核</td><td>总经理批准</td>
          </tr>
          <tr>
            <td><div class="ep-line"></div></td>
            <td><div class="ep-line"></div></td>
            <td><div class="ep-line"></div></td>
            <td><div class="ep-line"></div></td>
          </tr>
          <tr>
            <td>日期：</td><td>日期：</td><td>日期：</td><td>日期：</td>
          </tr>
        </table>

        <div class="ep-foot">
          {{ printData.code }} · 打印于 {{ printData.printed_at }} · 第 {{ printData.print_count + 1 }} 次打印
        </div>
      </div>
    </div>
  </div>
  `
};
