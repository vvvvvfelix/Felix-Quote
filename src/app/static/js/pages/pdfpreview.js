/* ============================ 组件：PDF 在线预览 ============================ */
/*   用法：<PdfPreview v-if="pdfView" :url="pdfView.url" :title="pdfView.title"
                       @close="pdfView=null" />                    */
const PdfPreview = {
  props: ['url', 'title', 'downloadUrl'],
  emits: ['close'],
  setup(props, { emit }) {
    const loading = ref(true), err = ref('');

    async function load() {
      loading.value = true; err.value = '';
      try {
        // 先 fetch 一次拿到 blob —— 直接用 <iframe src> 会带上浏览器的
        // PDF 插件行为，且加载失败时用户只看到空白框，没有提示。
        const tk = localStorage.getItem(TOKEN_KEY);
        const r = await fetch(props.url, {
          headers: tk ? { Authorization: 'Bearer ' + tk } : {},
        });
        if (!r.ok) throw new Error('加载失败 ' + r.status);
        const blob = await r.blob();
        if (blob.type && blob.type.includes('json')) {
          const j = JSON.parse(await blob.text());
          throw new Error(j.detail || '生成失败');
        }
        if (window.URL && window.URL.createObjectURL) {
          objectUrl.value = window.URL.createObjectURL(blob);
        } else {
          objectUrl.value = '';
        }
      } catch (e) { err.value = e.message; }
      finally { loading.value = false; }
    }

    const objectUrl = ref('');

    /* 用户在预览里点浏览器工具栏的打印按钮也能打印；
       这里再给一个明确的「打印」按钮，走隐藏 iframe 打印。*/
    function print() {
      const f = document.getElementById('pdfprintframe');
      if (!f) return;
      f.contentWindow.focus();
      f.contentWindow.print();
    }

    function download() {
      if (props.downloadUrl) api.download(props.downloadUrl).catch(e => notify(e.message, 'err'));
    }

    onMounted(load);
    onUnmounted(() => {
      if (objectUrl.value) window.URL.revokeObjectURL(objectUrl.value);
    });
    return { loading, err, objectUrl, print, download };
  },
  template: `
  <div class="mask pdf-mask" @click.self="$emit('close')">
    <div class="modal wide pdf-modal">
      <div class="modal-h"><h3>{{ title || 'PDF 预览' }}</h3>
        <span class="x-btn" @click="$emit('close')">×</span></div>
      <div class="pdf-body">
        <div v-if="loading" class="muted" style="padding:40px;text-align:center">正在生成…</div>
        <div v-else-if="err" class="warn-box" style="margin:20px">{{ err }}</div>
        <iframe v-else-if="objectUrl" :src="objectUrl" class="pdf-frame"></iframe>
        <!-- 打印用：把 PDF 再塞进隐藏 iframe，浏览器可完整排版打印 -->
        <iframe v-if="objectUrl" id="pdfprintframe" :src="objectUrl"
          style="position:absolute;width:0;height:0;border:0;opacity:0"></iframe>
      </div>
      <div class="modal-f">
        <button class="btn" @click="download" v-if="downloadUrl">另存为文件</button>
        <span class="grow"></span>
        <button class="btn" @click="print" :disabled="!objectUrl">打印</button>
        <button class="btn primary" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>`
};
