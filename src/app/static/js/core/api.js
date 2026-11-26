/* ============================ API 客户端 ============================ */
const TOKEN_KEY = 'wv_token';
async function req(method, url, body, isForm) {
  const opt = { method, headers: {} };
  const tk = localStorage.getItem(TOKEN_KEY);
  if (tk) opt.headers['Authorization'] = 'Bearer ' + tk;
  if (body !== undefined && body !== null) {
    if (isForm) opt.body = body;
    else { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(url, opt);
  const ct = r.headers.get('content-type') || '';
  if (!r.ok) {
    if (r.status === 402) {
      // 试用期已到：统一提示，前端据此展示激活引导
      let m = '试用期已到，请联系作者开通服务';
      if (ct.includes('json')) { try { const j = await r.json(); m = j.message || m; } catch (e) {} }
      const err = new Error(m); err.code = 'trial_expired'; throw err;
    }
    let msg = '请求失败 (' + r.status + ')';
    if (ct.includes('json')) { try { const j = await r.json(); msg = j.detail || msg; } catch (e) {} }
    throw new Error(msg);
  }
  return ct.includes('json') ? r.json() : r;
}
const api = {
  get: u => req('GET', u),
  post: (u, b) => req('POST', u, b),
  put: (u, b) => req('PUT', u, b),
  del: u => req('DELETE', u),
  async upload(url, file, onProgress, extra) {
    const fd = new FormData(); fd.append('file', file);
    if (extra) Object.keys(extra).forEach(k => fd.append(k, extra[k]));
    return new Promise((res, rej) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      const tk = localStorage.getItem(TOKEN_KEY);
      if (tk) xhr.setRequestHeader('Authorization', 'Bearer ' + tk);
      xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let j = null; try { j = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status >= 200 && xhr.status < 300) res(j);
        else rej(new Error((j && j.detail) || ('上传失败 ' + xhr.status)));
      };
      xhr.onerror = () => rej(new Error('网络错误'));
      xhr.send(fd);
    });
  },
  async download(url, filename) {
    const tk = localStorage.getItem(TOKEN_KEY);
    const r = await fetch(url, { headers: tk ? { Authorization: 'Bearer ' + tk } : {} });
    if (!r.ok) throw new Error('下载失败 ' + r.status);
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename || 'download.pdf';
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }
};
