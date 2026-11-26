/* ============================ 组件：模板管理 ============================ */
/* ======================================================================
   客户自带模板：上传对方给的报价单文件 → 点选绑定字段 → 按原格式出单
   ====================================================================== */
const ClientTplPage = {
  props: [], emits: [],
  setup() {
    const list = ref([]), loading = ref(true), up = reactive({ busy: false, drag: false, p: 0 });
    const tpl = ref(null);                 // 当前打开的模板（含 structure / field_map / item_map）
    const fields = ref([]), itemFields = ref([]);
    const customers = ref([]);
    const selRef = ref('');                // 当前选中的单元格坐标
    const filter = ref('');                // 单元格搜索
    const saving = ref(false);
    const quotes = ref([]), qPick = ref(0);
    const filled = ref(null);
    const custFilter = ref(0);
    const pickCust = ref(0);

    function shown() {
      if (!custFilter.value) return list.value;
      return list.value.filter(t => t.customer_id === custFilter.value);
    }

    function tplOfCust() {
      if (!pickCust.value) return [];
      return list.value.filter(t => t.customer_id === pickCust.value);
    }

    async function load() {
      loading.value = true;
      try { list.value = await api.get('/api/client-templates'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    async function loadRefs() {
      try {
        const r = await api.get('/api/client-templates/fields');
        fields.value = r.fields;
        itemFields.value = r.item_fields;
        const cs = await api.get('/api/customers?limit=300');
        customers.value = cs.items || cs || [];
        const qs = await api.get('/api/quotations?limit=100');
        quotes.value = qs || [];
      } catch (e) { notify(e.message, 'err'); }
    }

    async function upload(files) {
      const f = files && files[0];
      if (!f) return;
      up.busy = true; up.p = 5;
      try {
        const r = await api.upload('/api/client-templates', f, p => { up.p = p * 100; },
          { name: f.name.replace(/\.[^.]+$/, ''), customer_id: '' });
        notify(`已导入「${r.name}」，识别到 ${r.position_count} 个可填位置`, 'ok');
        load();
        await openById(r.id);
      } catch (e) { notify(e.message, 'err'); }
      finally { up.busy = false; up.p = 0; up.drag = false; }
    }
    function onDrop(e) { up.drag = false; upload(e.dataTransfer.files); }

    async function openById(id) {
      try {
        const r = await api.get('/api/client-templates/' + id);
        tpl.value = r;
        selRef.value = '';
        filter.value = '';
        // 每次打开都刷新报价单列表（可能刚在别处新建过）
        const qs = await api.get('/api/quotations?limit=100');
        quotes.value = qs || [];
        // 模板自带客户时，快捷条默认选中该客户
        if (r.customer_id) pickCust.value = r.customer_id;
      } catch (e) { notify(e.message, 'err'); }
    }

    async function open(t) { await openById(t.id); }

    /* ---- 单元格 ---- */
    function cells() {
      const st = tpl.value && tpl.value.structure;
      if (!st || st.kind !== 'excel') return [];
      const sheet = (st.sheets || []).find(s => s.name === (tpl.value.sheet_name || st.default_sheet))
        || (st.sheets || [])[0];
      if (!sheet) return [];
      let list = sheet.cells || [];
      const f = filter.value.trim().toLowerCase();
      if (f) list = list.filter(c => (c.text || '').toLowerCase().includes(f));
      return list;
    }
    function sheetName() {
      const st = tpl.value && tpl.value.structure;
      if (!st || st.kind !== 'excel') return '';
      const sheet = (st.sheets || []).find(s => s.name === (tpl.value.sheet_name || st.default_sheet))
        || (st.sheets || [])[0];
      return sheet ? sheet.name : '';
    }
    function fieldOf(ref) {
      const m = tpl.value && tpl.value.field_map;
      return (m && m[ref]) || '';
    }
    function bindField(ref, key) {
      if (!tpl.value) return;
      const m = Object.assign({}, tpl.value.field_map || {});
      if (key) m[ref] = key; else delete m[ref];
      tpl.value.field_map = m;
    }
    function fieldLabel(key) {
      const f = fields.value.find(x => x.key === key);
      return f ? f.label : key;
    }
    function itemLabel(key) {
      const f = itemFields.value.find(x => x.key === key);
      return f ? f.label : key;
    }
    function boundCount() {
      const m = (tpl.value && tpl.value.field_map) || {};
      return Object.keys(m).length;
    }
    function itemBoundCount() {
      const im = (tpl.value && tpl.value.item_map) || {};
      return Object.keys(im.cols || {}).length;
    }

    /* ---- 明细区 ---- */
    function setHeaderRow(v) {
      if (!tpl.value) return;
      const im = Object.assign({}, tpl.value.item_map || {});
      im.header_row = parseInt(v, 10) || 0;
      tpl.value.item_map = im;
    }
    function bindCol(colLetter, key) {
      if (!tpl.value) return;
      const im = Object.assign({}, tpl.value.item_map || {});
      const cols = Object.assign({}, im.cols || {});
      if (key) cols[colLetter] = key; else delete cols[colLetter];
      im.cols = cols;
      tpl.value.item_map = im;
    }
    function colOf(key) {
      const im = (tpl.value && tpl.value.item_map) || {};
      const cols = im.cols || {};
      const hit = Object.keys(cols).find(c => cols[c] === key);
      return hit || '';
    }
    /* 明细区用到的列（取自模板实际出现的列） */
    function gridCols() {
      const cs = cells();
      const set = new Set();
      cs.forEach(c => { const L = c.ref.replace(/\d+/g, ''); if (L) set.add(L); });
      return Array.from(set).sort();
    }

    async function save() {
      if (!tpl.value) return;
      saving.value = true;
      try {
        await api.put('/api/client-templates/' + tpl.value.id, {
          name: tpl.value.name, note: tpl.value.note,
          sheet_name: sheetName(),
          field_map: tpl.value.field_map || {},
          item_map: tpl.value.item_map || {},
        });
        notify('映射已保存', 'ok');
        load();
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }

    async function preview() {
      if (!tpl.value || !qPick.value) { notify('请先选择一份报价单', 'warn'); return; }
      saving.value = true;
      try {
        const r = await api.post('/api/client-templates/' + tpl.value.id + '/preview', {
          quotation_id: qPick.value,
          field_map: tpl.value.field_map || {},
          item_map: tpl.value.item_map || {},
          sheet_name: sheetName(),
        });
        notify(`试填完成：${r.field_count} 个字段、${r.item_rows} 行明细`, 'ok');
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }

    async function generate() {
      if (!tpl.value || !qPick.value) { notify('请先选择一份报价单', 'warn'); return; }
      saving.value = true;
      try {
        const r = await api.post('/api/quotations/' + qPick.value + '/fill-template',
          { template_id: tpl.value.id });
        filled.value = r;
        // 生成完顺手问一句：要不要存成该客户的专属模板
        saveAs.open = true;
        saveAs.name = (tpl.value.name || '') + '（客户版）';
        saveAs.customer_id = tpl.value.customer_id || 0;
        notify(`已生成：${r.name}`, 'ok');
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }

    function download() {
      if (filled.value && filled.value.file_id)
        window.open('/api/filled/' + encodeURIComponent(filled.value.file_id), '_blank');
    }

    /* ---- 生成后另存为客户专属模板 ---- */
    const saveAs = reactive({ open: false, name: '', customer_id: 0, note: '', busy: false });

    async function saveAsTemplate() {
      if (!filled.value || !filled.value.file_id) return;
      if (!saveAs.name.trim()) { notify('给模板起个名字', 'warn'); return; }
      saveAs.busy = true;
      try {
        const r = await api.post('/api/filled/save-as-template', {
          file_id: filled.value.file_id,
          name: saveAs.name.trim(),
          customer_id: saveAs.customer_id || null,
          note: saveAs.note || '',
        });
        notify(`已保存为客户模板：${r.name}`, 'ok');
        saveAs.open = false;
        load();
      } catch (e) { notify(e.message, 'err'); }
      finally { saveAs.busy = false; }
    }

    async function remove(t) {
      if (!confirm('删除模板「' + t.name + '」？已生成的报价单文件不受影响。')) return;
      try {
        await api.del('/api/client-templates/' + t.id);
        if (tpl.value && tpl.value.id === t.id) tpl.value = null;
        notify('已删除', 'ok');
        load();
      } catch (e) { notify(e.message, 'err'); }
    }

    function close() { tpl.value = null; }

    function custName(cid) {
      if (!cid) return '';
      const c = customers.value.find(x => x.id === cid);
      return c ? (c.company_en || c.company_cn || '') : '';
    }

    onMounted(async () => { await load(); await loadRefs(); });
    return {
      list, loading, up, tpl, fields, itemFields, customers, quotes, qPick,
      selRef, filter, saving, filled, saveAs, custFilter, shown,
      pickCust, tplOfCust,
      cells, sheetName, fieldOf, bindField, fieldLabel, itemLabel,
      boundCount, itemBoundCount, gridCols, colOf, setHeaderRow, bindCol,
      upload, onDrop, open, openById, save, preview, generate, download,
      saveAsTemplate, remove, close, notify, custName,
    };
  },
  template: `
  <div>
    <div class="card">
      <div class="card-h">
        <div><h3>客户自带报价单模板</h3>
          <div class="muted small">每个客户的报价单格式都不一样 —— 直接用对方给的文件出单，
            格式、边框、字体、列宽全部原样保留</div></div>
        <span class="tag ok">0 Token · 本地处理</span>
      </div>
      <div class="card-b">
        <div class="drop" :class="{over:up.drag}"
             @dragover.prevent="up.drag=true" @dragleave="up.drag=false"
             @drop.prevent="onDrop" @click="$refs.tf.click()">
          <input ref="tf" type="file" accept=".xlsx,.xlsm,.docx" style="display:none"
                 @change="upload($event.target.files); $event.target.value=''">
          <b>把客户的报价单模板拖进来，或点击选择文件</b>
          <div class="muted small">支持 .xlsx / .xlsm / .docx　·　建议先在模板里留出空白单元格待填</div>
          <div v-if="up.busy" style="margin-top:8px">上传解析中 {{ up.p }}%</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div><h3>已导入的模板</h3>
          <div class="muted small">共 {{ list.length }} 份　·　绑定客户后，出该客户的单会优先推荐</div></div>
        <select class="inp" v-model="custFilter" style="max-width:240px">
          <option :value="0">全部客户</option>
          <option v-for="c in customers" :key="c.id" :value="c.id">
            {{ c.company_en || c.company_cn }}
          </option>
        </select>
      </div>
      <div class="card-b tight">
        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!shown().length" class="empty">
          {{ list.length ? '该客户名下还没有模板' : '还没有导入客户模板' }}
        </div>
        <table v-else class="tbl responsive">
          <thead><tr><th>模板名</th><th>归属客户</th><th>类型</th>
            <th>已绑字段</th><th>明细列</th><th>导入时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="t in shown()" :key="t.id">
              <td data-label="模板名"><b>{{ t.name }}</b>
                <div v-if="t.note" class="muted small">{{ t.note }}</div></td>
              <td data-label="归属客户">
                <span v-if="t.customer_id" class="tag ok">{{ custName(t.customer_id) }}</span>
                <span v-else class="muted small">通用</span>
              </td>
              <td data-label="类型"><span class="tag">{{ t.file_type.toUpperCase() }}</span></td>
              <td data-label="已绑字段"><span v-if="t.field_count" class="tag ok">{{ t.field_count }} 个</span>
                  <span v-else class="muted">未配置</span></td>
              <td data-label="明细列"><span v-if="t.item_count" class="tag ok">{{ t.item_count }} 列</span>
                  <span v-else class="muted">未配置</span></td>
              <td class="muted small" data-label="导入时间">{{ (t.created_at||'').slice(0,10) }}</td>
              <td class="nowrap" data-label="操作">
                <button class="btn sm primary" @click="open(t)">配置映射</button>
                <button class="btn sm" @click="remove(t)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ===== 映射配置弹窗 ===== -->
    <div class="modal wide" v-if="tpl">
      <div class="modal-h">
        <div><b>{{ tpl.name }}</b>
          <div class="muted small">{{ tpl.file_name }}　·　已绑 {{ boundCount() }} 个字段 / {{ itemBoundCount() }} 个明细列</div></div>
        <button class="x-btn" @click="close()">×</button>
      </div>

      <div class="modal-b">
        <!-- 出单快捷入口：选客户 → 直接用该客户的模板 -->
        <div class="quick-bar">
          <span class="muted small">给哪个客户出单？</span>
          <select class="inp" v-model="pickCust" style="max-width:300px">
            <option :value="0">选择客户…</option>
            <option v-for="c in customers" :key="c.id" :value="c.id">
              {{ c.company_en || c.company_cn }}
            </option>
          </select>
          <template v-if="pickCust">
            <template v-if="tplOfCust().length">
              <span class="muted small">该客户的模板：</span>
              <button v-for="t in tplOfCust()" :key="t.id" class="btn sm"
                      :class="{ primary: tpl && tpl.id===t.id }"
                      @click="openById(t.id)">{{ t.name }}</button>
            </template>
            <span v-else class="tag" style="background:#FFF3D6;color:#8A6508">
              该客户还没有专属模板，可用下方通用模板
            </span>
          </template>
        </div>

        <div class="toolbar" style="margin:12px 0">
          <input class="inp" v-model="tpl.name" placeholder="模板名">
          <input class="inp grow" v-model="tpl.note" placeholder="备注（如：2026版 / 阿联酋客户专用）">
          <select class="inp" v-model="qPick">
            <option :value="0">选一份报价单来出单…</option>
            <option v-for="q in quotes" :key="q.id" :value="q.id">
              \${{ q.quote_no }} · \${{ q.title || '未命名' }} · USD \${{ q.total_amount }}
            </option>
          </select>
        </div>

        <div v-if="tpl.structure && tpl.structure.kind==='word'" class="tip">
          Word 模板：正文中写 <code v-pre>{{quote.quote_no}}</code> 这类占位符即可自动替换；
          明细表在下方指定「表格序号 + 起始行 + 各列字段」，行数不够会自动补行并保留格式。
        </div>
        <div v-else-if="tpl.structure" class="tip">
          Excel 模板：在 <b>左侧单元格</b> 点一个位置 → 右边选「要填什么字段」。
          也可以直接在单元格里写 <code v-pre>{{quote.remark}}</code> 这类占位符，系统会自动替换。
        </div>

        <div class="grid2" style="gap:14px;align-items:start">
          <!-- 左：单元格列表 -->
          <div v-if="tpl.structure && tpl.structure.kind==='excel'">
            <div class="sec-title">模板内容（点选要填的位置）</div>
            <div class="toolbar" style="margin-bottom:8px">
              <input class="inp grow" v-model="filter" placeholder="搜索单元格文字，如 Quotation No">
              <span class="muted small">工作表：{{ sheetName() }}</span>
            </div>
            <div style="max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:8px">
              <table class="tbl responsive" style="margin:0">
                <tbody>
                  <tr v-for="c in cells()" :key="c.ref"
                      :class="{ sel: selRef===c.ref, mapped: fieldOf(c.ref) }"
                      @click="selRef = c.ref">
                    <td style="width:64px" class="ref" data-label="单元格">{{ c.ref }}</td>
                    <td class="ell" data-label="内容">{{ c.text }}</td>
                    <td style="width:120px" class="mapped-tag" data-label="映射字段">
                      <span v-if="fieldOf(c.ref)" class="tag ok">{{ fieldLabel(fieldOf(c.ref)) }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-if="!cells().length" class="empty">没有匹配的内容</div>
            </div>
          </div>

          <!-- 右：字段绑定 + 明细区 -->
          <div>
            <div class="sec-title">字段绑定</div>
            <div style="padding:12px;background:#FAFBFC;border:1px solid var(--line);border-radius:8px">
              <div v-if="selRef" class="muted small" style="margin-bottom:8px">
                当前选中单元格 <b>{{ selRef }}</b> —— 选一个字段填进去：
              </div>
              <div v-else class="muted small" style="margin-bottom:8px">
                👈 先在左边点一个单元格位置
              </div>
              <select class="inp" style="width:100%" :value="fieldOf(selRef)"
                      :disabled="!selRef" @change="bindField(selRef, $event.target.value)">
                <option value="">（不填 / 清除）</option>
                <option v-for="f in fields" :key="f.key" :value="f.key">{{ f.label }}</option>
              </select>
              <div v-if="Object.keys(tpl.field_map||{}).length" style="margin-top:12px">
                <div class="muted small" style="margin-bottom:6px">已绑定的位置：</div>
                <div style="max-height:130px;overflow:auto">
                  <div v-for="(k,ref) in tpl.field_map" :key="ref"
                       style="display:flex;gap:8px;align-items:center;padding:3px 0">
                    <code style="min-width:56px">{{ ref }}</code>
                    <span class="muted small">{{ fieldLabel(k) }}</span>
                    <button class="btn xs" style="margin-left:auto"
                            @click="bindField(ref,'')">移除</button>
                  </div>
                </div>
              </div>
            </div>

            <div class="sec-title" style="margin-top:16px">产品明细表</div>
            <div style="padding:12px;background:#FAFBFC;border:1px solid var(--line);border-radius:8px">
              <div class="field" style="margin-bottom:10px">
                <label v-if="tpl.structure && tpl.structure.kind==='excel'">表头所在行号</label>
                <label v-else>表格起始行号</label>
                <input class="inp" type="number" min="0"
                       :value="(tpl.item_map&&tpl.item_map.header_row)||(tpl.item_map&&tpl.item_map.start_row)||0"
                       @input="tpl.item_map = Object.assign({}, tpl.item_map||{}, {header_row: +$event.target.value, start_row: +$event.target.value})">
                <div class="muted small">数据从这一行的<b>下一行</b>开始逐条写入</div>
              </div>
              <div class="muted small" style="margin:10px 0 6px">每一列填什么：</div>
              <div style="max-height:210px;overflow:auto">
                <div v-for="cl in gridCols()" :key="cl"
                     style="display:flex;gap:8px;align-items:center;padding:3px 0">
                  <code style="min-width:34px">{{ cl }}</code>
                  <select class="inp" style="flex:1" :value="colOf('')"
                          @change="()=>{}" v-show="false"></select>
                  <select class="inp" style="flex:1" :value="Object.entries((tpl.item_map&&tpl.item_map.cols)||{}).find(([k])=>k===cl)?.[1]||''"
                          @change="bindCol(cl, $event.target.value)">
                    <option value="">（不填该列）</option>
                    <option v-for="f in itemFields" :key="f.key" :value="f.key">{{ f.label }}</option>
                  </select>
                </div>
              </div>
              <div class="muted small" style="margin-top:8px">
                已绑 {{ itemBoundCount() }} 列。填表后新增行会自动沿用模板原有格式。
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-f">
        <button class="btn" @click="close()">取消</button>
        <button class="btn" @click="preview()" :disabled="saving || !qPick">试填一下</button>
        <button class="btn primary" @click="save()" :disabled="saving">保存映射</button>
        <button class="btn primary" @click="generate()" :disabled="saving || !qPick">
          生成报价单文件
        </button>
        <button class="btn ok" v-if="filled" @click="download()">下载生成的文件</button>
        <button class="btn ok" v-if="filled" @click="saveAs.open = true">
          存为该客户的模板
        </button>
      </div>
    </div>

    <!-- ===== 生成后：另存为该客户的专属模板 ===== -->
    <div class="modal" v-if="saveAs.open">
      <div class="modal-h">
        <div><b>存为客户专属模板</b>
          <div class="muted small">下次给这个客户出单，直接选它就行，不用重配</div></div>
        <button class="x-btn" @click="saveAs.open = false">×</button>
      </div>
      <div class="modal-b">
        <div class="field">
          <label>模板名</label>
          <input class="inp" v-model="saveAs.name" placeholder="如：AL NOUR 2026 版">
        </div>
        <div class="field" style="margin-top:12px">
          <label>归属客户</label>
          <select class="inp" v-model="saveAs.customer_id">
            <option :value="0">不指定（通用模板）</option>
            <option v-for="c in customers" :key="c.id" :value="c.id">
              {{ c.company_en || c.company_cn }}{{ c.country ? '（'+c.country+'）' : '' }}
            </option>
          </select>
          <div class="muted small">绑定后，这份模板会在该客户出单时优先推荐</div>
        </div>
        <div class="field" style="margin-top:12px">
          <label>备注</label>
          <input class="inp" v-model="saveAs.note" placeholder="如：阿联酋客户专用 / 2026 版">
        </div>
        <div class="tip" style="margin-top:14px">
          保存的是<b>刚生成的那份文件本身</b>（含已填数据与全部格式），
          同时继承当前模板的字段映射。以后复用时，数据会按新报价单重新填。
        </div>
      </div>
      <div class="modal-f">
        <button class="btn" @click="saveAs.open = false">暂不保存</button>
        <button class="btn primary" @click="saveAsTemplate()" :disabled="saveAs.busy">
          保存为客户模板
        </button>
      </div>
    </div>
  </div>
  `,
};
