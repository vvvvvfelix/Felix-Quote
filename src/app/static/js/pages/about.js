/* ============================ 组件：关于与授权声明 ============================ */
const AboutPage = {
  setup() {
    const d = ref(null), loading = ref(true);
    onMounted(async () => {
      try { d.value = await api.get('/api/about'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    });
    const copy = t => {
      const txt = t;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(() => notify('已复制：' + txt, 'ok'), () => notify('复制失败，请手动选择', 'err'));
      } else { notify('当前环境不支持自动复制，请手动选择文本', 'err'); }
    };
    return { d, loading, copy, notify };
  },
  template: `
  <div v-if="loading" class="empty"><div class="ic">◌</div><div class="t">加载中…</div></div>
  <div v-else-if="d">
    <div class="card" style="background:linear-gradient(120deg,#fff,#F5F8FF);border-color:#D6E1F5">
      <div class="card-b">
        <div style="display:flex;align-items:center;gap:15px;flex-wrap:wrap">
          <div style="width:50px;height:50px;border-radius:13px;background:#E8EFFB;display:grid;place-items:center;font-size:24px">©</div>
          <div style="flex:1;min-width:260px">
            <div style="font-size:11.5px;color:#8B90A0;font-weight:600;letter-spacing:.5px">SOFTWARE OWNERSHIP</div>
            <div style="font-size:19px;font-weight:800;margin-top:5px">软件著作权归属</div>
            <div style="font-size:12.5px;color:#5A6070;margin-top:5px;line-height:1.8">
              本软件（含全部源代码、界面、文档、预置数据）著作权归
              <b style="color:#B8860B">{{ d.owner }}</b> 所有。
              {{ d.company_cn }} 为软件的使用单位，非著作权人。
            </div>
          </div>
          <div style="text-align:right">
            <div style="font-size:11px;color:#8B90A0">权属登记号</div>
            <div class="mono" style="font-size:14px;font-weight:700;letter-spacing:.5px">{{ d.owner_id }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><div class="lb">著作权人</div><div class="vl" style="font-size:22px">{{ d.owner }}</div>
        <div class="sf">{{ d.owner_role }}</div></div>
      <div class="stat g"><div class="lb">当前版本</div><div class="vl" style="font-size:22px">v{{ d.version }}</div>
        <div class="sf">{{ d.product_cn }}</div></div>
      <div class="stat k"><div class="lb">版权年份</div><div class="vl" style="font-size:22px">{{ d.year }}</div>
        <div class="sf">All Rights Reserved</div></div>
      <div class="stat b"><div class="lb">授权状态</div><div class="vl" style="font-size:19px;color:#1F9D5C">正版</div>
        <div class="sf">本机安装已登记</div></div>
    </div>

    <div class="split">
      <div class="card">
        <div class="card-h"><div><h3>版权与许可</h3><div class="d">Copyright &amp; License</div></div></div>
        <div class="card-b">
          <div style="background:#F7F8FB;border:1px solid #E6E9F2;border-radius:9px;padding:13px;font-size:12.5px;line-height:1.9;color:#3A4055">
            {{ d.copyright }}
          </div>
          <div style="margin-top:11px;font-size:12px;color:#5A6070;line-height:1.9">
            未经<b>{{ d.owner }}</b>书面许可，任何单位或个人不得对本软件进行复制、二次分发、
            转售、破解、反编译、去水印或用于任何商业再授权行为。授权仅限{{ d.company_cn }}
            内部及其指定客户范围内使用。
          </div>
          <div style="margin-top:13px;display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn sm" @click="copy(d.copyright)">复制版权声明</button>
            <button class="btn sm" @click="copy('著作权人：' + d.owner + '\\n权属登记号：' + d.owner_id + '\\n版权声明：' + d.copyright)">复制完整署名</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-h"><div><h3>归属举证指纹</h3><div class="d">用于证明本软件由 {{ d.owner }} 独立开发</div></div></div>
        <div class="card-b tight">
          <div class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>核心文件</th><th>SHA256（前 24 位）</th></tr></thead>
            <tbody><tr v-for="f in d.fingerprint.files" :key="f.name">
              <td class="mono" data-label="核心文件">{{ f.name }}</td>
              <td class="mono small muted" data-label="SHA256（前 24 位）">{{ f.sha256.slice(0,24) }}…</td>
            </tr>
            <tr><td class="mono b" data-label="核心文件">整机指纹</td><td class="mono small b" data-label="SHA256（前 24 位）">{{ d.fingerprint.core_sha256.slice(0,24) }}…</td></tr>
            <tr><td class="mono" data-label="核心文件">本机安装号</td><td class="mono small" data-label="SHA256（前 24 位）">{{ d.install_id }}</td></tr>
            <tr><td class="mono" data-label="核心文件">运行环境</td><td class="mono small" data-label="SHA256（前 24 位）">Python {{ d.fingerprint.python }} · {{ d.fingerprint.platform }}</td></tr>
            </tbody>
          </table></div>
          <div style="padding:10px 14px;font-size:11.5px;color:#8B90A0;border-top:1px solid #EDEFF4">
            如需维权举证，可将本页截图连同源码目录一并提交。核心文件哈希与本文档随版本发布一同留存。
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>责任声明（免责声明）</h3><div class="d">使用本软件即视为已阅读并同意以下条款</div></div></div>
      <div class="card-b">
        <div v-for="(x,i) in d.disclaimer" :key="i"
             style="display:flex;gap:12px;padding:11px 0;border-bottom:1px dashed #EDEFF4">
          <div style="flex:0 0 96px;font-size:12px;font-weight:700;color:#B8860B">{{ x.title }}</div>
          <div style="flex:1;font-size:12.5px;line-height:1.85;color:#4A5065">{{ x.text }}</div>
        </div>
        <div style="margin-top:14px;font-size:12px;color:#8B90A0;line-height:1.9;text-align:right">
          {{ d.copyright }}<br>{{ d.owner }} · {{ d.owner_id }}
        </div>
      </div>
    </div>
  </div>`
};
