/* ============================ 组件：报价单打印（签字盖章用） ============================ */
const PrintQuotePage = {
  props: ['quoteId'],
  setup(props) {
    const html = ref(''), loading = ref(true), err = ref('');
    const frame = ref(null);

    async function load() {
      loading.value = true; err.value = '';
      try {
        const r = await fetch('/api/quotations/' + props.quoteId + '/preview', {
          headers: { Authorization: 'Bearer ' + localStorage.getItem(TOKEN_KEY) },
        });
        if (!r.ok) throw new Error('加载失败 ' + r.status);
        const raw = await r.text();
        html.value = injectPrintCss(raw);
      } catch (e) { err.value = e.message; }
      finally { loading.value = false; }
    }

    /* iframe 是独立文档，不继承父页面样式 —— 打印规则必须注入进去 */
    function injectPrintCss(src) {
      const css = `<style id="__print">
        @page{size:A4;margin:12mm 10mm}
        html,body{background:#fff!important;margin:0!important;padding:0!important}
        table{page-break-inside:auto}
        tr{page-break-inside:avoid}
        thead{display:table-header-group}
        tfoot{display:table-footer-group}
        h1,h2,h3{page-break-after:avoid}
        @media screen{ body{padding:24px} }
      </style>`;
      if (/<\/head>/i.test(src)) return src.replace(/<\/head>/i, css + '</head>');
      return css + src;
    }

    function print() {
      const f = frame.value;
      if (!f) return;
      // 直接打印 iframe 内容，避免弹窗拦截
      f.contentWindow.focus();
      f.contentWindow.print();
    }

    onMounted(load);
    return { html, loading, err, frame, print, load };
  },
  template: `
  <div>
    <div class="print-bar no-print">
      <button class="btn" @click="$emit('back')">← 返回</button>
      <b>打印报价单</b>
      <span class="muted small">打印后签字盖章 → 扫描 → 到客户页点邮箱发送</span>
      <span class="grow"></span>
      <button class="btn primary" :disabled="loading || err" @click="print">🖨 打印</button>
    </div>
    <div v-if="loading" class="content muted">加载中…</div>
    <div v-else-if="err" class="content warn-box">{{ err }}</div>
    <iframe v-show="!loading && !err" ref="frame" class="print-frame"
      :srcdoc="html" style="width:100%;height:calc(100vh - 120px);border:0;background:#fff"></iframe>
  </div>`
};
