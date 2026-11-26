/* ============================ 根组件 ============================ */
const RootApp = {
  setup() {
    const user = ref(null), booting = ref(true);
    const page = ref('dashboard'), loadingQuoteId = ref(null);
    /* 移动端抽屉导航：汉堡键唤出，遮罩/切页关闭 */
    const drawerOpen = ref(false);
    function toggleDrawer() { drawerOpen.value = !drawerOpen.value; }
    function closeDrawer() { drawerOpen.value = false; }
    watch(drawerOpen, v => { document.body.classList.toggle('drawer-open', v); });
    // 切页即收起抽屉（手机上点任意导航项都会改 page，自动关闭）
    watch(page, () => { if (drawerOpen.value) drawerOpen.value = false; });
    /* 支持用 URL 锚点直达某个页面（桌面控制台图标打开 #console 直接进管理页） */
    const _hash = (location.hash || '').replace(/^#/, '');
    if (/^[a-zA-Z]+$/.test(_hash)) page.value = _hash;
    const loginMode = ref('login');
    const loginForm = reactive({ username: '', password: '', email: '', full_name: '', company: '' });
    const loginErr = ref(''), loginBusy = ref(false);
    const categories = ref([]), suppliers = ref([]), company = ref({});
    const templates = ref([]);
    /* 归属信息由后端 /api/about 统一下发，避免前后端署名不一致 */
    const about = ref(null);
    /* 企业化：我的档案（角色/数据范围/权限点）+ 组织用户列表 + 待办角标 */
    const me = ref(null);
    const orgUsers = ref([]);
    const openTasks = ref(0);
    /* 打印视图：记录正在打印的报价单 id */
    const printQuoteId = ref(null);
    /* 邮件发送：从报价单管理页直接发信时用 */
    const mailQuote = ref(null);
    /* 侧栏低频分组折叠状态 —— 存 localStorage，下次打开保持上次的选择。
       管理员默认展开「系统设置」：组织与权限、发件设置是管理员必用入口，
       藏得太深反而找不到。 */
    const foldKey = 'wv_sb_folds';
    const foldOpen = reactive(readFolds());
    function readFolds() {
      try {
        const s = JSON.parse(localStorage.getItem(foldKey) || 'null');
        if (s && typeof s === 'object') return { ops: !!s.ops, data: !!s.data, sys: !!s.sys };
      } catch (e) { /* 存储损坏就用默认值 */ }
      return { ops: false, data: false, sys: false };
    }
    function saveFolds() {
      try { localStorage.setItem(foldKey, JSON.stringify(foldOpen)); } catch (e) { /* 无痕模式 */ }
    }
    /* 首次登录（没存过折叠偏好）时：管理员默认展开「系统设置」 */
    function applyFoldDefaults(u) {
      let saved = null;
      try { saved = localStorage.getItem(foldKey); } catch (e) { /* 无痕模式 */ }
      if (!saved && u && u.role === 'admin') foldOpen.sys = true;
      // 当前页在某个折叠组里 → 把那一组展开，避免「点不到当前页」
      if (page.value === 'production' || page.value === 'finance' || page.value === 'tasks')
        foldOpen.ops = true;
      else if (page.value === 'products' || page.value === 'suppliers' ||
               page.value === 'documents' || page.value === 'ctpl' ||
               page.value === 'templates') foldOpen.data = true;
      else if (page.value === 'org' || page.value === 'structure' ||
               page.value === 'position' ||
               page.value === 'mailsettings' ||
               page.value === 'connectors' ||
               page.value === 'company' || page.value === 'usage') foldOpen.sys = true;
    }
    function toggleFold(k) { foldOpen[k] = !foldOpen[k]; saveFolds(); }
    /* PDF 在线预览 */
    const pdfView = ref(null);
    const copyYear = computed(() => (about.value && about.value.year) || '2026');
    const ver = computed(() => (about.value && about.value.version) || '1.1.0');
    const ownerId = computed(() => (about.value && about.value.owner_id) || 'WV-2026-FELIX-0001');
    /* M006 可配置署名：登录页 / 侧栏 / 标题全部从 /api/about 取生效值，杜绝前后端署名不一致 */
    const appBrand = computed(() => (about.value && about.value.brand) || 'Felixv');
    const appProductCn = computed(() => (about.value && about.value.product_cn) || 'Felix 企业管理平台');
    const appProductEn = computed(() => (about.value && about.value.product_en) || 'Felix · Enterprise Management Platform');

    /* 组织切换器（M004）：可切换的组织节点 > 1 个时才显示 */
    const canSwitchOrg = computed(() =>
      !!(me.value && me.value.switch_options && me.value.switch_options.length > 1));
    async function switchOrg(idRaw) {
      const id = Number(idRaw);
      if (!me.value || id === me.value.current_org_unit_id) return;
      try {
        const r = await api.post('/api/org/switch', { org_unit_id: id });
        const opt = (me.value.switch_options || []).find(o => o.id === id);
        me.value.current_org_unit_id = id;
        me.value.current_org_name = opt ? opt.name : me.value.current_org_name;
        notify('已切换当前组织：' + (opt ? opt.name : ''), 'ok');
        // 范围口径已变：若停留在组织类页面，强制重载其数据
        if (page.value === 'structure' || page.value === 'position'
            || page.value === 'employee') {
          const cur = page.value;
          page.value = 'dashboard';
          await nextTick();
          page.value = cur;
        }
      } catch (e) { notify(e.message, 'err'); }
    }

    /* 授权/试用状态（登录页与关于页也会用到，故放全局） */
    const licenseState = ref(null);
    async function loadLicense() {
      try { licenseState.value = await api.get('/api/licensing/status'); } catch (e) { /* 静默 */ }
    }
    const licenseBanner = computed(() => {
      const s = licenseState.value; if (!s) return null;
      if (s.mode === 'trial' && !s.expired) {
        const d = s.days_left;
        return { type: 'info', text: '试用版 · 剩余 ' + (d != null ? d : '?') + ' 天，到期后请联系作者开通服务', canActivate: false };
      }
      if (s.expired) {
        return { type: 'err', text: s.message + '（管理员进入「授权激活」页面输入授权码）', canActivate: (user.value && user.value.role === 'admin') };
      }
      if (s.activated) return { type: 'ok', text: '已授权' + (s.owner ? '（' + s.owner + '）' : ''), canActivate: false };
      return null;
    });

    function has(perm) {
      const ps = (me.value && me.value.permissions) || [];
      if (ps.includes('*')) return true;
      if (ps.includes(perm)) return true;
      const i = perm.lastIndexOf('.');
      return i > 0 && ps.includes(perm.slice(0, i));
    }

    /* 待办角标：30 秒轮询一次。VPN 场景够用，且断线重连零成本。 */
    let badgeTimer = null;
    async function refreshBadge() {
      try {
        const r = await api.get('/api/dashboard/today');
        openTasks.value = (r.task_summary && r.task_summary.open) || 0;
      } catch (e) { /* 静默：角标失败不影响主流程 */ }
    }
    function startBadge() {
      if (badgeTimer) clearInterval(badgeTimer);
      refreshBadge();
      badgeTimer = setInterval(refreshBadge, 30000);
    }
    function stopBadge() { if (badgeTimer) { clearInterval(badgeTimer); badgeTimer = null; } }

    /* 从报价单管理页点「发邮件」：取出该单的客户，打开发送窗口 */
    async function openMail(q) {
      if (!q.customer_id) {
        notify('这份报价单没有关联客户，请先在报价单中指定客户', 'warn');
        return;
      }
      try {
        const c = await api.get('/api/customers/' + q.customer_id);
        mailQuote.value = { customer: c, quoteId: q.id };
      } catch (e) { notify(e.message, 'err'); }
    }

    async function loadRefs() {
      const [c, s, comp, t] = await Promise.all([
        api.get('/api/categories').catch(() => []),
        api.get('/api/suppliers').catch(() => []),
        api.get('/api/company').catch(() => ({})),
        api.get('/api/templates').catch(() => [])
      ]);
      categories.value = c; suppliers.value = s; company.value = comp; templates.value = t;
      me.value = await api.get('/api/org/me').catch(() => null);
      if (me.value && has('users.view')) {
        orgUsers.value = await api.get('/api/org/users').catch(() => []);
      }
    }
    async function submitLogin() {
      loginErr.value = ''; loginBusy.value = true;
      try {
        const url = loginMode.value === 'login' ? '/api/auth/login' : '/api/auth/register';
        const payload = loginMode.value === 'login'
          ? { username: loginForm.username, password: loginForm.password } : { ...loginForm };
        const r = await api.post(url, payload);
        localStorage.setItem(TOKEN_KEY, r.token);
        user.value = r.user;
        applyFoldDefaults(r.user);
        await loadRefs();
        loadLicense();
        startBadge();
        notify('欢迎，' + (r.user.full_name || r.user.username), 'ok', '登录成功');
      } catch (e) { loginErr.value = e.message; }
      finally { loginBusy.value = false; }
    }
    function logout() {
      localStorage.removeItem(TOKEN_KEY);
      user.value = null; me.value = null; orgUsers.value = [];
      openTasks.value = 0; stopBadge();
      page.value = 'dashboard';
    }
    const pageTitle = computed(() => ({
      dashboard: ['工作台', '数据概览与快捷入口'],
      products: ['产品库', '自有产品与供应商配件'],
      builder: ['报价单制作', '选品 → 生成报价单 → 导出 PDF'],
      quotations: ['报价单管理', '历史报价单与导出'],
      customers: ['客户管理', '海外客户资料'],
      templates: ['报价模板', 'Jinja2 模板定制'],
      documents: ['资料库', 'PDF / TXT / XLSX 资料导入'],
      suppliers: ['供应商', '自有产线与外协厂商'],
      company: ['公司信息', '主体资质与证书'],
      users: ['用户管理', '账号与权限'],
      usage: ['Token 用量', '本地 0 Token 计量'],
      about: ['关于与授权', '软件著作权归属与责任声明'],
      tasks: ['待办中心', '跨部门派活与我的待办'],
      inquiries: ['询盘管理', '业务入口 · 登记与跟进'],
      orders: ['销售订单', '成交后的履约与跨部门确认'],
      production: ['生产看板', '在制订单进度'],
      finance: ['财务应收', '应收台账与收汇'],
      org: ['组织与权限', '部门 · 用户 · 角色 · 操作日志'],
      structure: ['组织树', '集团 / 法人公司 / 工厂 / 事业部 / 部门 / 车间 / 班组'],
      position: ['岗位职级', '岗位 · 职级(M/P/T/O) · 编制 · 汇报线 · 编制 vs 在岗看板'],
      employee: ['员工档案', '档案 · 合同 · 社保 · 银行卡 · 全生命周期异动流水'],
      workflow: ['审批中心', '待办 / 已办 / 我发起的 / 流程监控 · 请假示例'],
      expense: ['电子报销', '我的报销单 / 新建向导（边填边查重）/ 财务复核台 / A4 打印'],
      mailsettings: ['发件设置', '配置你的邮箱，一键给客户发报价单'],
      connectors: ['连接器', '企业微信 / 钉钉 / 飞书 SSO 登录配置'],
      printquote: ['打印报价单', '打印后签字盖章，扫描即可发邮件'],
      deptboard: ['部门看板', '按组织架构查看各部门功能与人员'],
      report: ['报表与打印', '报价单 / 送货单 / 行程单 / 发票 / 水单报销'],
      toolbox: ['岗位工具箱', '各岗位常用外部软件与系统集成指引'],
      licensing: ['授权激活', '试用期状态与授权码激活']
    }[page.value] || [(about.value && about.value.product_cn) || 'Felix 企业管理平台', '']));

    async function wecomSso() {
      // 企业微信内置浏览器 SSO（M120）：未登录静默跳授权，回调带 code 时换系统 token
      const ua = navigator.userAgent || '';
      if (!/MicroMessenger|wxwork/i.test(ua)) return null;   // 非企微环境直接跳过
      const params = new URLSearchParams(location.search);
      const code = params.get('code');
      const tk = localStorage.getItem(TOKEN_KEY);
      if (!tk && code) {
        // 授权回调：用 code 换系统 token，走既有登录态（不重复弹登录框）
        try {
          const r = await api.get('/api/connectors/wecom/callback?code=' + encodeURIComponent(code));
          if (r.ok && r.token) {
            localStorage.setItem(TOKEN_KEY, r.token);
            const url = new URL(location.href);
            url.searchParams.delete('code'); url.searchParams.delete('state');
            history.replaceState({}, '', url.pathname + url.search);
            return 'authed';   // 已拿到 token，继续下方既有登录态加载
          } else if (!r.matched) {
            notify(r.message || '企业微信账号未绑定', 'warn');
          }
        } catch (e) { /* 网络/配置异常，回退普通登录 */ }
      }
      if (!tk && !code) {
        // 未登录且非回调：拉授权地址跳转
        try {
          const r = await api.get('/api/connectors/wecom/authorize-url?redirect_uri='
            + encodeURIComponent(location.href));
          if (r.ok && r.url) { window.location.href = r.url; return 'redirect'; }
        } catch (e) { /* 连接器未启用则忽略 */ }
      }
      return null;
    }

    onMounted(async () => {
      try { about.value = await api.get('/api/about'); } catch (e) { /* 未登录时可匿名读取 */ }
      loadLicense();
      const sso = await wecomSso().catch(() => null);
      if (sso === 'redirect') { booting.value = false; return; }   // 已跳走，终止当前启动
      const tk = localStorage.getItem(TOKEN_KEY);
      if (tk) {
        try { user.value = await api.get('/api/auth/me'); applyFoldDefaults(user.value); await loadRefs(); startBadge(); }
        catch (e) { localStorage.removeItem(TOKEN_KEY); }
      }
      booting.value = false;
    });

    return {
      user, booting, page, pageTitle, drawerOpen, toggleDrawer, closeDrawer,
      categories, suppliers, company, templates,
      me, orgUsers, openTasks, has,
      printQuoteId, mailQuote, foldOpen, pdfView, toggleFold,
      loginMode, loginForm, loginErr, loginBusy, loadingQuoteId, toastList,
      copyYear, ver, ownerId, canSwitchOrg, switchOrg,
      submitLogin, logout, loadRefs, notify, fmtMoney, certName, fmtSize
    };
  },
  template: `
  <div v-if="booting" style="display:grid;place-items:center;height:100vh;color:#8B90A0">载入中…</div>

  <div v-else-if="!user" class="login-wrap">
    <div class="login-hero">
      <div>
        <div class="lh-brand">{{ appBrand }}</div>
        <div class="lh-cn">{{ appProductCn }}</div>
        <div class="lh-en">{{ appProductEn }}</div>
      </div>
      <div class="lh-mid">
        <div class="lh-feat"><div class="lh-ico">▤</div><div>
          <b>商用厨房 / 烘焙设备全库报价</b>
          <div style="font-size:11.5px;color:#9AA1B1;margin-top:3px">层炉 · 旋转炉 · 隧道炉 · 蒸烤箱 · 搅拌机 · 醒发箱，含规格材质净重毛重</div>
        </div></div>
        <div class="lh-feat"><div class="lh-ico">✎</div><div>
          <b>按你的模板生成报价单</b>
          <div style="font-size:11.5px;color:#9AA1B1;margin-top:3px">Jinja2 模板引擎 + 一键导出 PDF，条款/证书/图片自动填充</div>
        </div></div>
        <div class="lh-feat"><div class="lh-ico">⇪</div><div>
          <b>资料自动入库（PDF / TXT / DOCX / XLSX）</b>
          <div style="font-size:11.5px;color:#9AA1B1;margin-top:3px">本地解析供应商资料，自动抽取型号规格重量价格证书</div>
        </div></div>
        <div class="lh-feat"><div class="lh-ico">◈</div><div>
          <b>本地 0 Token</b>
          <div style="font-size:11.5px;color:#9AA1B1;margin-top:3px">数据全部落在本地 SQLite，不联网不消耗额度；启用云端增强时才实时计量</div>
        </div></div>
        <div class="lh-feat"><div class="lh-ico">©</div><div>
          <b>按客户模板出单 · 保留原格式</b>
          <div style="font-size:11.5px;color:#9AA1B1;margin-top:3px">上传客户的 Excel/Word 报价单模板，映射字段后一键生成，字体边框列宽全部保留</div>
        </div></div>
      </div>
      <div class="lh-ft">
        生产基地：广州市白云区白云湖街唐阁绿道中116号<br>
        Tel +86 20 3667 7767 &nbsp;|&nbsp; 400 001 1367 &nbsp;|&nbsp; www.example.com
      </div>
    </div>
    <div class="login-panel">
      <div class="login-card">
        <h2>{{ loginMode==='login'?'登录系统':'创建账号' }}</h2>
        <div class="sub">请使用公司邮箱或管理员分配的账号登录</div>
        <div class="tabs">
          <button :class="{on:loginMode==='login'}" @click="loginMode='login'">登录</button>
          <button :class="{on:loginMode==='register'}" @click="loginMode='register'">注册</button>
        </div>
        <form @submit.prevent="submitLogin">
          <div class="field"><label>用户名 <span class="req">*</span></label>
            <input class="inp" v-model="loginForm.username" placeholder="至少 3 个字符"></div>
          <div class="field"><label>密码 <span class="req">*</span></label>
            <input class="inp" type="password" v-model="loginForm.password" placeholder="至少 6 位"></div>
          <template v-if="loginMode==='register'">
            <div class="grid2">
              <div class="field"><label>姓名</label><input class="inp" v-model="loginForm.full_name"></div>
              <div class="field"><label>邮箱</label><input class="inp" type="email" v-model="loginForm.email"></div>
            </div>
            <div class="field"><label>公司</label><input class="inp" v-model="loginForm.company" placeholder="菲力克斯科技（广州）有限公司"></div>
          </template>
          <div v-if="loginErr" style="color:#D3453B;font-size:12px;margin-bottom:12px">{{ loginErr }}</div>
          <button class="btn primary lg" :disabled="loginBusy" type="submit">{{ loginBusy?'处理中…':'进入系统' }}</button>
        </form>
        <div class="hint">初始管理员：<code>admin</code> / <code>wv2026</code> — 登录后请立即修改密码。</div>
        <div class="lh-own">© {{ copyYear }} Felix · {{ ownerId }} · v{{ ver }}<br>
          著作权归作者所有，未经许可不得复制或转售</div>
      </div>
    </div>
  </div>

  <div v-else class="shell">
    <aside class="sidebar">
      <div class="sb-logo"><div class="b">{{ appBrand }}</div><div class="n">{{ appProductCn }}</div></div>
      <nav class="sb-nav">
        <!-- 高频：始终可见 -->
        <div class="sb-item" :class="{on:page==='dashboard'}" @click="page='dashboard'"><i class="ic">▦</i><span>工作台</span><i class="bd" v-if="openTasks">{{ openTasks }}</i></div>
        <div class="sb-item" :class="{on:page==='builder'}" @click="page='builder'"><i class="ic">✎</i><span>报价单制作</span></div>
        <div class="sb-item" :class="{on:page==='quotations'}" @click="page='quotations'"><i class="ic">▤</i><span>报价单管理</span></div>
        <div class="sb-item" :class="{on:page==='inquiries'}" @click="page='inquiries'"><i class="ic">◉</i><span>询盘管理</span></div>
        <div class="sb-item" :class="{on:page==='orders'}" @click="page='orders'"><i class="ic">▧</i><span>销售订单</span></div>
        <div class="sb-item" :class="{on:page==='customers'}" @click="page='customers'"><i class="ic">◍</i><span>客户管理</span></div>

        <!-- 部门目录：按组织架构分组，点开看全部功能 -->
        <div class="sb-item" :class="{on:page==='deptboard'}" @click="page='deptboard'"><i class="ic">▤</i><span>部门看板</span></div>

        <!-- 低频：折叠 -->
        <div class="sb-fold" :class="{on: foldOpen.ops}" @click="toggleFold('ops')">
          <i class="ic">{{ foldOpen.ops ? '▾' : '▸' }}</i><span>运营与生产</span>
        </div>
        <div v-show="foldOpen.ops">
          <div class="sb-item" :class="{on:page==='tasks'}" @click="page='tasks'"><i class="ic">☐</i><span>待办中心</span><i class="bd" v-if="openTasks">{{ openTasks }}</i></div>
          <div class="sb-item" :class="{on:page==='production'}" @click="page='production'"><i class="ic">⚙</i><span>生产看板</span></div>
          <div class="sb-item" :class="{on:page==='finance'}" v-if="has('finance.view')" @click="page='finance'"><i class="ic">¥</i><span>财务应收</span></div>
          <div class="sb-item" :class="{on:page==='report'}" v-if="has('reports.view')" @click="page='report'"><i class="ic">▣</i><span>报表与打印</span></div>
          <div class="sb-item" :class="{on:page==='toolbox'}" @click="page='toolbox'"><i class="ic">🧰</i><span>岗位工具箱</span></div>
        </div>

        <div class="sb-fold" :class="{on: foldOpen.data}" @click="toggleFold('data')">
          <i class="ic">{{ foldOpen.data ? '▾' : '▸' }}</i><span>基础资料</span>
        </div>
        <div v-show="foldOpen.data">
          <div class="sb-item" :class="{on:page==='products'}" @click="page='products'"><i class="ic">▤</i><span>产品库</span></div>
          <div class="sb-item" :class="{on:page==='suppliers'}" @click="page='suppliers'"><i class="ic">◫</i><span>供应商</span></div>
          <div class="sb-item" :class="{on:page==='documents'}" @click="page='documents'"><i class="ic">⇪</i><span>资料库</span></div>
          <div class="sb-item" :class="{on:page=='ctpl'}" @click="page='ctpl'"><i class="ic">⌘</i><span>客户模板</span></div>
          <div class="sb-item" :class="{on:page=='templates'}" @click="page='templates'"><i class="ic">▦</i><span>PDF模板</span></div>
        </div>

        <div class="sb-fold" :class="{on: foldOpen.sys}" @click="toggleFold('sys')">
          <i class="ic">{{ foldOpen.sys ? '▾' : '▸' }}</i><span>系统设置</span>
        </div>
        <div v-show="foldOpen.sys">
          <div class="sb-item" :class="{on:page==='structure'}" v-if="has('structure.view')" @click="page='structure'"><i class="ic">⌸</i><span>组织树</span></div>
          <div class="sb-item" :class="{on:page==='position'}" v-if="has('position.view')" @click="page='position'"><i class="ic">◈</i><span>岗位职级</span></div>
          <div class="sb-item" :class="{on:page==='employee'}" v-if="has('employee.view')" @click="page='employee'"><i class="ic">👤</i><span>员工档案</span></div>
          <div class="sb-item" :class="{on:page==='workflow'}" v-if="has('workflow.view')" @click="page='workflow'"><i class="ic">☑</i><span>审批中心</span></div>
          <div class="sb-item" :class="{on:page==='expense'}" v-if="has('expense.view')" @click="page='expense'"><i class="ic">🧾</i><span>电子报销</span></div>
          <div class="sb-item" :class="{on:page==='org'}" v-if="has('users.view')" @click="page='org'"><i class="ic">◉</i><span>组织与权限</span></div>
          <div class="sb-item" :class="{on:page==='console'}" v-if="me && me.role==='admin'" @click="page='console'"><i class="ic">⛭</i><span>管理控制台</span></div>
          <div class="sb-item" :class="{on:page==='licensing'}" v-if="me && me.role==='admin'" @click="page='licensing'"><i class="ic">🔑</i><span>授权激活</span></div>
          <div class="sb-item" :class="{on:page==='mailsettings'}" @click="page='mailsettings'"><i class="ic">✉</i><span>发件设置</span></div>
          <div class="sb-item" :class="{on:page==='connectors'}" @click="page='connectors'"><i class="ic">⇄</i><span>连接器</span></div>
          <div class="sb-item" :class="{on:page==='company'}" @click="page='company'"><i class="ic">◈</i><span>公司信息</span></div>
          <div class="sb-item" :class="{on:page==='usage'}" @click="page='usage'"><i class="ic">◔</i><span>Token 用量</span></div>
        </div>
      </nav>
      <div class="sb-foot">
        <div style="color:#D5D9E2;font-weight:600">{{ user.full_name||user.username }}</div>
        <div style="margin:3px 0 9px">{{ user.company||'—' }}</div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span class="tag" :class="user.role==='admin'?'gold':'blue'">{{ user.role==='admin'?'管理员':'销售' }}</span>
          <a style="color:#8B90A0;cursor:pointer" @click="logout">退出</a>
        </div>
        <div class="sb-copy" @click="page='about'" title="查看软件著作权归属与责任声明">© {{ copyYear }} Felix · v{{ ver }} · 权属登记号 {{ ownerId }}</div>
      </div>
    </aside>

    <div class="scrim" @click="closeDrawer"></div>

    <main class="main">
      <div class="topbar">
        <div style="display:flex;align-items:center;min-width:0">
          <button class="hb" @click="toggleDrawer" aria-label="菜单">☰</button>
          <div style="min-width:0"><h1>{{ pageTitle[0] }}</h1><div class="sub">{{ pageTitle[1] }}</div></div>
        </div>
        <div style="display:flex;gap:9px;align-items:center">
          <select v-if="canSwitchOrg" class="inp sm" style="max-width:180px"
                  :value="me ? me.current_org_unit_id : null"
                  @change="switchOrg($event.target.value)"
                  title="切换当前组织（数据范围随组织变化）">
            <option v-for="o in (me ? me.switch_options : [])" :key="o.id" :value="o.id">
              {{ o.org_type === 'company' ? '★ ' : (o.org_type === 'factory' ? '▣ ' : '◆ ') }}{{ o.name }}</option>
          </select>
          <span v-if="me && me.current_org_name" class="tag" style="background:#FFF4E0;color:#8A6508">
            {{ me.current_org_name }}</span>
          <span class="tag ok">本地模式 · 0 Token</span>
          <button class="btn sm" @click="page='builder'">＋ 快速报价</button>
        </div>
      </div>
      <div v-if="licenseBanner" class="lic-banner" :class="licenseBanner.type">
        <span>{{ licenseBanner.text }}</span>
        <button v-if="licenseBanner.canActivate" class="btn sm" @click="page='licensing'">去激活</button>
      </div>
      <div class="content">
        <DashboardPage v-if="page==='dashboard'" :categories="categories" @nav="p=>page=p"/>
        <ProductsPage v-else-if="page==='products'" :categories="categories" :suppliers="suppliers"/>
        <BuilderPage v-else-if="page==='builder'" ref="builder"
          :categories="categories" :templates="templates" @saved="page='quotations'"/>
        <QuotationsPage v-else-if="page==='quotations'"
          @edit="qid=>{page='builder'; nextTick(()=>$refs.builder.loadQuote(qid));}"
          @new="()=>{page='builder'; nextTick(()=>$refs.builder.resetForm());}"
          @print="qid=>{printQuoteId=qid; page='printquote';}"
          @mail="q=>openMail(q)"
          @preview="q=>pdfView={url:'/api/quotations/'+q.id+'/pdf?inline=1',
                                title:'报价单预览 — '+q.quote_no,
                                downloadUrl:'/api/quotations/'+q.id+'/pdf'}"/>
        <CustomersPage v-else-if="page==='customers'" @send="c=>mailQuote={customer:c}"/>
        <ClientTplPage v-else-if="page==='ctpl'"/>
        <TemplatesPage v-else-if="page==='templates'" @changed="loadRefs"/>
        <DocumentsPage v-else-if="page==='documents'" :categories="categories"/>
        <SuppliersPage v-else-if="page==='suppliers'"/>
        <CompanyPage v-else-if="page==='company'" :company="company" @saved="loadRefs"/>
        <UsersPage v-else-if="page==='users'"/>
        <UsagePage v-else-if="page==='usage'"/>
        <AboutPage v-else-if="page==='about'"/>
        <TaskPage v-else-if="page==='tasks'"/>
        <InquiryPage v-else-if="page==='inquiries'" :customers="customers" :users="orgUsers"/>
        <OrderPage v-else-if="page==='orders'" :customers="customers" :users="orgUsers"/>
        <ProductionPage v-else-if="page==='production'"/>
        <FinancePage v-else-if="page==='finance'"/>
        <OrgPage v-else-if="page==='org'"/>
        <StructurePage v-else-if="page==='structure'"/>
        <PositionPage v-else-if="page==='position'"/>
        <EmployeePage v-else-if="page==='employee'"/>
        <WorkflowPage v-else-if="page==='workflow'"/>
        <ExpensePage v-else-if="page==='expense'"/>
        <MailSettingsPage v-else-if="page==='mailsettings'"/>
        <ConnectorsPage v-else-if="page==='connectors'"/>
        <DeptBoardPage v-else-if="page==='deptboard'" @nav="k=>page=k"/>
        <ConsolePage v-else-if="page==='console'"/>
        <PrintQuotePage v-else-if="page==='printquote'" :quoteId="printQuoteId"
          @back="page='quotations'"/>
        <ReportPage v-else-if="page==='report'"/>
        <ToolboxPage v-else-if="page==='toolbox'"/>
        <LicensingPage v-else-if="page==='licensing'"/>
      </div>
      <SendQuoteModal v-if="mailQuote" :customer="mailQuote.customer"
        :presetQuoteId="mailQuote.quoteId"
        @close="mailQuote=null" @sent="()=>{mailQuote=null;}"/>
      <PdfPreview v-if="pdfView" :url="pdfView.url" :title="pdfView.title"
        :downloadUrl="pdfView.downloadUrl" @close="pdfView=null"/>
    </main>
  </div>

  <div id="toast-root"><div v-for="t in toastList" :key="t.id" class="toast" :class="t.type">
    <b v-if="t.title">{{ t.title }}</b>{{ t.msg }}</div></div>
  `
};

const _app = createApp({
  components: {
    DashboardPage, ProductsPage, BuilderPage, QuotationsPage, CustomersPage,
    ClientTplPage, TemplatesPage, DocumentsPage, SuppliersPage, CompanyPage,
    UsersPage, UsagePage, AboutPage,
    InquiryPage, OrderPage, TaskPage, FinancePage, OrgPage, StructurePage,
    PositionPage, EmployeePage, WorkflowPage, ProductionPage, ExpensePage,
    MailSettingsPage, ConnectorsPage, PrintQuotePage, DeptBoardPage, ConsolePage,
    ReportPage, ToolboxPage, LicensingPage
  },
  ...RootApp
});
/* 通用工具注册为全局属性：所有页面模板可直接用，不必每个页面重复 return。
   漏 return 的话模板里会报 "xxx is not a function"，整块内容渲染不出来。 */
Object.assign(_app.config.globalProperties, SHARED_HELPERS);
/* 弹窗类组件做全局注册：任何页面都能直接用 <SendQuoteModal>，
   不必在每个页面模板里重复声明 components。 */
_app.component('SendQuoteModal', SendQuoteModal);
_app.component('PdfPreview', PdfPreview);
_app.mount('#app');

/* PWA 预埋缝：仅在生产态（非 localhost / 非 file:）注册，开发态零副作用 */
if ('serviceWorker' in navigator && location.hostname !== 'localhost'
    && location.protocol !== 'file:') {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  });
}
