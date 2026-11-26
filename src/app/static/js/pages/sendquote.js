/* ============================ 组件：发送报价单（客户页唤起） ============================ */
const SendQuoteModal = {
  props: ['customer', 'presetQuoteId'],
  emits: ['close', 'sent'],
  setup(props, { emit }) {
    const quotes = ref([]), loading = ref(true), sending = ref(false);
    const cfg = reactive({ configured: false, sender: '' });
    const form = reactive({
      quotation_id: null, to: '', cc: '', subject: '', body: '',
      attach_pdf: true, signed: null,
    });
    const previewLoading = ref(false);

    async function load() {
      loading.value = true;
      try {
        const [q, c] = await Promise.all([
          api.get('/api/mail/quotations?customer_id=' + props.customer.id),
          api.get('/api/mail/config'),
        ]);
        quotes.value = q.items || [];
        cfg.configured = !!c.configured;
        cfg.sender = c.sender || '';
        if (quotes.value.length) {
          // 从报价单页发起时预选那一份，否则选最新一份
          const pre = quotes.value.find(x => x.id === props.presetQuoteId);
          form.quotation_id = pre ? pre.id : quotes.value[0].id;
          await loadPreview();
        }
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    /* 切换报价单时刷新默认主题与正文 */
    async function loadPreview() {
      if (!form.quotation_id) return;
      previewLoading.value = true;
      try {
        const p = await api.get('/api/mail/quotations/' + form.quotation_id + '/body');
        form.subject = p.subject;
        form.body = p.body;
        if (!form.to) form.to = p.to || '';
      } catch (e) { notify(e.message, 'err'); }
      finally { previewLoading.value = false; }
    }

    function pickQuote(q) { form.quotation_id = q.id; loadPreview(); }

    /* 上传已签字盖章的扫描件 */
    async function onSigned(e) {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      if (f.size > 8 * 1024 * 1024) return notify('扫描件不能超过 8MB', 'err');
      try {
        const r = await api.upload('/api/mail/upload-signed', f);
        form.signed = r;
        notify('扫描件已附加：' + r.name, 'ok');
      } catch (err) { notify(err.message, 'err'); }
    }
    function dropSigned() { form.signed = null; }

    async function send() {
      if (!form.quotation_id) return notify('请先选择要发送的报价单', 'warn');
      if (!form.to.trim()) return notify('请填写收件人邮箱', 'warn');
      sending.value = true;
      try {
        const body = {
          customer_id: props.customer.id,
          quotation_id: form.quotation_id,
          to: form.to.trim(), cc: form.cc.trim(),
          subject: form.subject, body: form.body,
          attach_pdf: form.attach_pdf,
          signed_name: form.signed ? form.signed.name : '',
          signed_mime: form.signed ? form.signed.mime : '',
          signed_b64: form.signed ? form.signed.b64 : '',
        };
        const r = await api.post('/api/mail/send-quotation', body);
        notify('已发送至 ' + r.to + '（' + r.quotation_no + '）', 'ok');
        emit('sent', r);
        emit('close');
      } catch (e) { notify(e.message, 'err'); }
      finally { sending.value = false; }
    }

    onMounted(load);
    return { quotes, loading, sending, cfg, form, previewLoading,
             loadPreview, pickQuote, onSigned, dropSigned, send, notify };
  },
  template: `
  <div class="mask" @click.self="$emit('close')">
    <div class="modal wide">
      <div class="modal-h"><h3>发送报价单 — {{ customer.company_en }}</h3>
        <span class="x-btn" @click="$emit('close')">×</span></div>
      <div class="modal-b">

        <div v-if="!cfg.configured" class="warn-box" style="margin-bottom:13px">
          <b>还没有配置发件邮箱。</b>
          请到「系统 → 发件设置」填写你的邮箱与授权码（支持 QQ / 163 / Gmail /
          Outlook / 企业邮箱），配置完成后即可一键发送。
          <div class="muted small" style="margin-top:5px">发件配置是每个账号独立的，
          业务员用自己的邮箱发送，不会互相影响。</div>
        </div>

        <div class="sec-title">① 选择报价单</div>
        <div v-if="loading" class="muted small">加载中…</div>
        <div v-else-if="!quotes.length" class="warn-box">
          该客户还没有报价单。请先到「报价单制作」生成一份，再回来发送。
        </div>
        <div v-else class="mail-quote-list">
          <div v-for="q in quotes" :key="q.id" class="qi"
               :class="{on: form.quotation_id===q.id}" @click="pickQuote(q)">
            <span class="mono">{{ q.quote_no }}</span>
            <span class="grow">{{ q.title||'（无标题）' }}</span>
            <span class="muted small">{{ q.currency }} {{ q.total_amount }}</span>
          </div>
        </div>

        <div class="sec-title" style="margin-top:15px">② 收件与内容</div>
        <div class="grid2">
          <div class="field" style="margin:0"><label>收件人 <span class="req">*</span></label>
            <input class="inp" v-model="form.to" placeholder="客户邮箱"></div>
          <div class="field" style="margin:0"><label>抄送（可选，逗号分隔）</label>
            <input class="inp" v-model="form.cc" placeholder="如 boss@company.com"></div>
        </div>
        <div class="field" style="margin-top:11px"><label>主题</label>
          <input class="inp" v-model="form.subject"></div>
        <div class="field" style="margin-top:11px"><label>正文</label>
          <textarea class="inp" rows="9" v-model="form.body"
            style="font-size:12.5px;line-height:1.7"></textarea></div>

        <div class="sec-title" style="margin-top:15px">③ 附件</div>
        <label class="chk" style="display:block;margin-bottom:8px">
          <input type="checkbox" v-model="form.attach_pdf">
          附加系统生成的报价单 PDF（自动生成，无需手动导出）
        </label>
        <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
          <label class="btn sm" style="cursor:pointer">
            上传已签字盖章的扫描件<input type="file" accept=".pdf,.png,.jpg,.jpeg"
              style="display:none" @change="onSigned">
          </label>
          <span v-if="form.signed" class="att-chip">
            {{ form.signed.name }} · {{ (form.signed.size/1024).toFixed(0) }} KB
            <a class="x-btn" style="color:inherit" @click="dropSigned">×</a>
          </span>
          <span v-else class="muted small">
            打印后签字盖章 → 扫描成 PDF/图片 → 在此上传，两份一起发给客户
          </span>
        </div>
      </div>
      <div class="modal-f">
        <span class="muted small grow" v-if="cfg.sender">发件邮箱：{{ cfg.sender }}</span>
        <button class="btn" @click="$emit('close')">取消</button>
        <button class="btn primary" :disabled="sending || loading || !quotes.length"
          @click="send">{{ sending?'发送中…':'发送邮件' }}</button>
      </div>
    </div>
  </div>`
};
