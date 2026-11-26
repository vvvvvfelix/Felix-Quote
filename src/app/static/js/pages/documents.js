/* ============================ 组件：资料库 ============================ */
const DocumentsPage = {
  props: ['categories'],
  setup(props) {
    const list = ref([]), loading = ref(true), up = reactive({ p: 0, busy: false, drag: false });
    const detail = ref(null);
    const editing = ref([]);
    /* ---- OCR 状态 ---- */
    const ocr = reactive({
      available: false, engine: '', busy: false, drag: false,
      result: null, cat: '', families: [], diag: null, fail: null
    });

    async function load() {
      loading.value = true;
      try { list.value = await api.get('/api/documents'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function upload(files) {
      if (!files || !files.length) return;
      up.busy = true;
      try {
        let total = 0;
        for (const f of Array.from(files)) {
          const r = await api.upload('/api/documents', f, p => { up.p = p * 100; });
          total += (r.count || 0);
          if (r.status === 'failed') notify(f.name + '：' + r.message, 'err');
        }
        notify('解析完成，共识别 ' + total + ' 条产品记录（可在下方确认入库）', total ? 'ok' : 'warn');
        load();
      } catch (e) { notify(e.message, 'err'); }
      finally { up.busy = false; up.p = 0; }
    }
    function onDrop(e) { up.drag = false; upload(e.dataTransfer.files); }

    /* ---- 图片 OCR ---- */
    async function initOcr() {
      try {
        ocr.diag = await api.get('/api/ocr/status');
        ocr.available = !!ocr.diag.available;
        ocr.engine = ocr.diag.os ? ocr.diag.os.desc : '';
      } catch (e) {
        ocr.available = false;
        ocr.diag = { available: false, reason: '无法连接服务', solution: '请确认服务已启动' };
      }
    }
    async function uploadImage(files) {
      const f = files && files[0];
      if (!f) return;
      ocr.busy = true; ocr.result = null; ocr.fail = null;
      try {
        const q = ocr.cat ? ('?category_code=' + encodeURIComponent(ocr.cat)) : '';
        const r = await api.upload('/api/ocr/image' + q, f);
        if (r.ok === false) {
          // 服务端诊断结果：明确告知原因与处理方法
          ocr.fail = r;
          notify(r.reason || '图片识别不可用', 'err');
          return;
        }
        ocr.result = r;
        ocr.cat = r.category_code;
        notify(`识别完成：${r.line_count} 行 / 置信度 ${r.avg_conf}，判定品类「${r.param_template.label}」`, 'ok');
        load();
      } catch (e) { notify(e.message, 'err'); }
      finally { ocr.busy = false; ocr.drag = false; }
    }
    function onImgDrop(e) {
      ocr.drag = false;
      const f = e.dataTransfer.files;
      if (f.length && !/^image\//.test(f[0].type)) {
        return notify('请上传图片文件（截图/照片）', 'warn');
      }
      uploadImage(f);
    }
    async function commitOcr() {
      const c = ocr.result && ocr.result.candidate;
      if (!c || !c.sku) return notify('未识别到可用字段，请检查图片清晰度', 'warn');
      try {
        const r = await api.post('/api/documents/' + ocr.result.doc_id + '/commit',
          { items: [c] });
        notify('已入库：' + c.name_cn + '（' + (r.inserted ? '新增' : 'SKU 已存在，跳过') + '）', 'ok');
        ocr.result = null; load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function initOcr2() {
      try { ocr.families = await api.get('/api/ocr/families'); } catch (e) {}
    }

    async function open(d) {
      try {
        const r = await api.get('/api/documents/' + d.id);
        detail.value = r;
        editing.value = JSON.parse(JSON.stringify(r.candidates || []));
      } catch (e) { notify(e.message, 'err'); }
    }
    async function commit() {
      try {
        const r = await api.post('/api/documents/' + detail.value.id + '/commit', { items: editing.value });
        notify('已入库 ' + r.inserted + ' 条，跳过 ' + r.skipped + ' 条重复', 'ok');
        detail.value = null; load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function reparse(d) {
      try { const r = await api.post('/api/documents/' + d.id + '/parse', {}); notify(r.message, 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function remove(d) {
      if (!confirm('删除文档「' + d.filename + '」？')) return;
      try { await api.del('/api/documents/' + d.id); notify('已删除', 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    onMounted(() => { load(); initOcr(); initOcr2(); });
    return {
      list, loading, up, detail, editing, ocr,
      upload, onDrop, uploadImage, onImgDrop, commitOcr,
      open, commit, reparse, remove, fmtSize, notify, categories: props.categories
    };
  },
  template: `
  <div>
    <!-- ============ 图片 OCR：截图 / 照片 ============ -->
    <div class="card" style="border-color:#EFE3C8;background:linear-gradient(120deg,#fff,#FDFBF5)">
      <div class="card-h">
        <div><h3>图片识别 · 截图 / 照片</h3>
          <div class="d">微信截图、产品照片、纸质报价单拍照 → 自动识别型号规格材质净重毛重价格证书</div></div>
        <div style="display:flex;gap:8px;align-items:center">
          <span class="tag" :class="ocr.available ? 'ok' : 'warn'">
            {{ ocr.available ? 'OCR 引擎就绪' : 'OCR 不可用' }}</span>
          <span class="tag ok">0 Token · 本地推理</span>
        </div>
      </div>
      <div class="card-b">
        <div v-if="!ocr.available" style="padding:14px 16px;background:#FDF6E8;border-radius:8px;
          font-size:12.5px;color:#6B4E0E;margin-bottom:14px;line-height:1.85">
          <div style="font-weight:600;color:#8A5A00;margin-bottom:6px">图片识别当前不可用</div>
          <div><b>原因：</b>{{ (ocr.diag && ocr.diag.reason) || '原因未知' }}</div>
          <div style="margin-top:5px"><b>处理方法：</b>{{ (ocr.diag && ocr.diag.solution) || '请运行「检查依赖.bat」查看详情' }}</div>
          <div style="margin-top:8px;padding-top:8px;border-top:1px dashed #E0CFA0;color:#8A6410">
            其余功能不受影响：产品库、客户管理、报价单制作、PDF 导出、Excel/PDF 资料导入均可正常使用。
          </div>
        </div>
        <div style="display:flex;gap:11px;align-items:flex-end;margin-bottom:13px;flex-wrap:wrap">
          <div class="field" style="margin:0;min-width:190px">
            <label>品类（留空则自动识别）</label>
            <select class="inp" v-model="ocr.cat">
              <option value="">🤖 自动识别品类</option>
              <option v-for="c in categories" :key="c.code" :value="c.code">{{ c.name_cn }}</option>
            </select>
          </div>
          <div style="font-size:11.5px;color:#8B90A0;flex:1;min-width:220px;line-height:1.7">
            不同品类的规格参数不同，选择品类后会套用对应的字段体系：
            烤箱看层数盘数/控温范围，搅拌机看缸容量/调速方式，冷藏看温度范围/制冷剂…
          </div>
        </div>
        <div class="drop" :class="{drag:ocr.drag}" style="padding:28px"
          @click="$refs.imgFile.click()"
          @dragover.prevent="ocr.drag=true" @dragleave="ocr.drag=false" @drop.prevent="onImgDrop">
          <div class="ic">🖼</div>
          <div class="b" style="font-size:13.5px">
            {{ ocr.busy ? '识别中，请稍候…' : '点击选择图片，或将截图/照片拖到此处' }}</div>
          <div class="muted small" style="margin-top:5px">
            支持 JPG / PNG / WEBP / BMP — 微信聊天截图、产品实拍、纸质报价单拍照均可</div>
          <div class="progress" v-if="ocr.busy"><i style="width:100%"></i></div>
          <input ref="imgFile" type="file" accept="image/*" style="display:none"
            @change="uploadImage($event.target.files);$event.target.value=''" :disabled="!ocr.available">
        </div>
      </div>
    </div>

    <!-- OCR 识别失败原因 -->
    <div v-if="ocr.fail" class="card" style="border-color:#F09595;background:#FEF7F6">
      <div class="card-h">
        <div><h3 style="color:#A32D2D">图片识别失败</h3>
          <div class="d">以下是服务端的诊断结果</div></div>
        <button class="btn sm" @click="ocr.fail=null">关闭</button>
      </div>
      <div class="card-b">
        <div class="grid2">
          <div>
            <div class="sec-title">原因</div>
            <div style="padding:11px 13px;background:#fff;border-radius:7px;
              font-size:12.5px;color:#791F1F;line-height:1.8">{{ ocr.fail.reason }}</div>
          </div>
          <div>
            <div class="sec-title">处理方法</div>
            <div style="padding:11px 13px;background:#fff;border-radius:7px;
              font-size:12.5px;color:#2C2C2A;line-height:1.8">{{ ocr.fail.solution }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- OCR 识别结果 -->
    <div v-if="ocr.result" class="card" style="border-color:#C5DCC8">
      <div class="card-h">
        <div><h3>识别结果</h3>
          <div class="d">{{ ocr.result.line_count }} 行文本 · 平均置信度 {{ ocr.result.avg_conf }}
            · 判定品类「{{ ocr.result.param_template.label }}」（参数体系 {{ ocr.result.param_template.params.length }} 项）</div></div>
        <div class="btn-row">
          <button class="btn sm" @click="ocr.result=null">关闭</button>
          <button class="btn primary sm" @click="commitOcr">✓ 确认入库</button>
        </div>
      </div>
      <div class="card-b">
        <div class="split">
          <div>
            <div class="sec-title">原图</div>
            <img :src="ocr.result.image_url" style="width:100%;max-height:320px;object-fit:contain;
              border:1px solid #E3E5EA;border-radius:8px;background:#FAFAFA">
            <div class="sec-title">识别文本（可核对）</div>
            <pre class="code" style="max-height:240px">{{ ocr.result.text }}</pre>
          </div>
          <div>
            <div class="sec-title">抽取到的字段</div>
            <div v-if="!ocr.result.candidate || !ocr.result.candidate.sku"
              style="padding:14px;background:#FDF4E3;border-radius:8px;font-size:12px;color:#8A6410;line-height:1.7">
              未能识别出完整字段。请上传更清晰的图片，或手动在「产品库」录入。
            </div>
            <table v-else class="tbl responsive">
              <thead><tr><th style="width:38%">字段</th><th>识别值</th></tr></thead>
              <tbody>
                <tr v-for="(v,k) in ocr.result.candidate" :key="k">
                  <td v-if="v && typeof v !== 'object' && v !== ''" data-label="字段">{{ k }}</td>
                  <td v-if="v && typeof v !== 'object' && v !== ''" class="b mono" data-label="识别值">{{ v }}</td>
                </tr>
                <tr v-if="ocr.result.candidate.certs && ocr.result.candidate.certs.length">
                  <td data-label="字段">certs（证书）</td>
                  <td data-label="识别值"><span v-for="c in ocr.result.candidate.certs" :key="c" class="tag gold"
                    style="margin-right:3px">{{ c }}</span></td>
                </tr>
              </tbody>
            </table>
            <div style="margin-top:11px;padding:11px 13px;background:var(--gold-l);
              border-radius:8px;font-size:11.5px;color:#5A6070;line-height:1.8">
              <b>入库后归属品类：</b>{{ ocr.result.category_code }} →
              套用「{{ ocr.result.param_template.label }}」参数体系：<br>
              <span v-for="p in ocr.result.param_template.params" :key="p.key"
                class="tag" style="margin:2px 3px 2px 0">{{ p.label_cn }}{{ p.unit?'('+p.unit+')':'' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>导入资料</h3>
        <div class="d">支持 PDF / TXT / DOCX / XLSX / CSV — 本地解析，自动抽取型号、规格、材质、净重毛重、证书、价格</div></div>
        <span class="tag ok">0 Token · 本地解析</span></div>
      <div class="card-b">
        <div class="drop" :class="{drag:up.drag}" @click="$refs.file.click()"
          @dragover.prevent="up.drag=true" @dragleave="up.drag=false" @drop.prevent="onDrop">
          <div class="ic">⇪</div>
          <div class="b" style="font-size:13.5px">点击选择文件，或将文件拖到此处</div>
          <div class="muted small" style="margin-top:5px">
            供应商报价表、产品目录、规格书、认证文件均可 · 单个文件建议 &lt; 20MB</div>
          <div class="progress" v-if="up.busy"><i :style="{width:up.p+'%'}"></i></div>
          <input ref="file" type="file" multiple style="display:none"
            accept=".pdf,.txt,.md,.csv,.docx,.xlsx,.xls,.json" @change="upload($event.target.files);$event.target.value=''">
        </div>
        <div style="margin-top:13px;padding:12px 14px;background:#FAFBFC;border-radius:8px;
          font-size:11.5px;color:#5A6070;line-height:1.85">
          <b>字段识别规则</b>（表头自动匹配中英文）：SKU / 型号 / 品名 / 英文名称 / 材质 / 功率电压 /
          层数盘数 / 外形尺寸 / 净重 / 毛重 / 人民币价 / FOB USD价 / MOQ / 交期 / HS编码 / 证书 / 关键词
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>文档列表</h3><div class="d">共 {{ list.length }} 份资料</div></div></div>
      <div class="card-b tight">
        <div v-if="!list.length && !loading" class="empty"><div class="ic">⇪</div>
          <div class="t">资料库为空</div><div>导入供应商报价单或产品目录后，产品会自动抽取</div></div>
        <div v-for="d in list" :key="d.id" class="doc-item">
          <div class="doc-ico">{{ (d.doc_type||'FILE').toUpperCase().slice(0,4) }}</div>
          <div style="flex:1;min-width:0">
            <div class="nm ell">{{ d.filename }}</div>
            <div class="mt">{{ fmtSize(d.size) }} · {{ (d.created_at||'').slice(0,16) }} ·
              <span class="tag" :class="{parsed:'ok',text_only:'warn',failed:'err'}[d.status]||''">
                {{ {pending:'待解析',parsed:'已解析',text_only:'仅文本',failed:'失败'}[d.status]||d.status }}</span>
              <span class="muted">{{ d.message }}</span></div>
          </div>
          <div class="btn-row">
            <button class="btn sm" @click="open(d)">查看/入库</button>
            <button class="btn sm" @click="reparse(d)">重新解析</button>
            <button class="btn sm danger" @click="remove(d)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 解析结果 -->
    <div v-if="detail" class="mask" @click.self="detail=null">
      <div class="modal wide">
        <div class="modal-h"><h3>{{ detail.filename }} — 解析结果</h3>
          <span class="x-btn" @click="detail=null">×</span></div>
        <div class="modal-b">
          <div style="padding:11px 14px;background:var(--gold-l);border-radius:8px;margin-bottom:14px;
            font-size:12px;line-height:1.7">
            {{ detail.message }} — 共 <b>{{ editing.length }}</b> 条候选记录。
            可直接编辑后确认入库；留空 SKU 的行会被跳过。
          </div>
          <div v-if="!editing.length" class="empty"><div class="ic">◌</div>
            <div class="t">未识别出结构化产品</div>
            <div>文档可能是扫描件或非表格排版。可在「产品库」手工录入，或重新解析。</div></div>
          <div v-else class="tbl-wrap">
            <table class="tbl responsive">
              <thead><tr><th style="width:110px">SKU</th><th style="min-width:140px">品名</th>
                <th>规格/材质</th><th class="r" style="width:60px">净重</th><th class="r" style="width:60px">毛重</th>
                <th class="r" style="width:76px">USD价</th><th style="width:110px">证书</th><th style="width:32px"></th></tr></thead>
              <tbody><tr v-for="(c,i) in editing" :key="i">
                <td data-label="SKU"><input v-model="c.sku" class="inp" style="padding:4px 6px"></td>
                <td data-label="品名"><input v-model="c.name_cn" class="inp" style="padding:4px 6px;font-weight:600">
                  <input v-model="c.name_en" class="inp" style="padding:4px 6px;font-size:11px;margin-top:2px"></td>
                <td class="small" data-label="规格/材质">{{ c.spec_en||c.spec||'—' }}</td>
                <td class="r" data-label="净重"><input v-model.number="c.net_weight" type="number" class="inp" style="padding:4px 6px;text-align:right"></td>
                <td class="r" data-label="毛重"><input v-model.number="c.gross_weight" type="number" class="inp" style="padding:4px 6px;text-align:right"></td>
                <td class="r" data-label="USD价"><input v-model.number="c.price_usd" type="number" class="inp" style="padding:4px 6px;text-align:right"></td>
                <td data-label="证书"><input :value="(c.certs||[]).join(',')" class="inp" style="padding:4px 6px;font-size:11px"
                  @input="c.certs = $event.target.value.split(/[,，;；\/、\\s]+/).filter(Boolean)"></td>
                <td class="rm" data-label="操作" style="width:32px;text-align:center;color:#D3453B;cursor:pointer"
                  @click="editing.splice(i,1)">×</td>
              </tr></tbody>
            </table>
          </div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="detail=null">取消</button>
          <button class="btn primary" :disabled="!editing.length" @click="commit">确认入库</button>
        </div>
      </div>
    </div>
  </div>`
};
