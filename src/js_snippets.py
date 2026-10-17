# -*- coding: utf-8 -*-
"""注入页面执行的全部 JS 片段。

集中放在一个文件里，因为它们共享同一套 DOM 约定：改一处结构往往要连带改几个片段。
按用途分成四组：

    一、页面探测       DUMP_PAGE / PAGE_META
    二、组展开         EXPAND_ONE
    三、按钮标记       TAG_ACTION / TAG_ACTION_SCAN / CLEAR_ATTR / TAG_MORE_ROW
    四、弹窗与回执     READ_MODAL / READ_NET_CELLS / FILL_UNITS
                       TAG_CONFIRM / TAG_CANCEL / VERIFY_BOX / READ_SNACKBAR

## 统一的返回约定

每个片段都 `return JSON.stringify({...})`。不要返回对象——CDP 的
`returnByValue` 对对象序列化在某些嵌套下会丢字段，字符串最稳。

## 统一的标记属性约定

写操作走「JS 打标记 → CDP 按属性选择器点击」两步。
标记属性名由调用方传入（`data-ajjoin` / `data-ajopt` / `data-ajchev` /
`data-ajconfirm` / `data-ajcancel` / `data-ajmore`）。

⚠️ **每次打标记前必须先清旧标记**。否则 CDP 会命中文档里第一个残留标记元素，
表现为「点了一个看起来完全无关的地方」，而且无异常、无报错。
"""

# ============================================================
# 一、页面探测
# ============================================================

# 一次拿回整页所有商品组的完整快照。
#
# 设计要点：
#   · 一次 querySelectorAll 拿完，不在 Python 侧反复 round-trip
#     （单次 exec_js 约 1~3s，一页 25 个组逐个读会到分钟级）
#   · 文本统一 `\s+` 归一 + 截断，避免超长文本撑爆 JSON 体积
#   · campaign 里同时给出 btns / activated / scheduled，
#     让 Python 侧不必再回页面查状态
DUMP_PAGE = r"""
(function(){
  var groups = document.querySelectorAll('.sc-list-row-group');
  var out = {groups: []};
  for (var i=0;i<groups.length;i++){
    var g = groups[i];
    var aside = g.querySelector('.sc-list-row-group__aside');

    // ⚠️ 展开后箭头类名会从 sc-ui-chevron--down 变成 --up（实测），
    //    原来只查 --down → 展开后 has_chevron 变 False → 整组落到「无箭头」分支
    //    → 把无 SKU 的聚合父行也当链接行判定（报告里的噪音就是这么来的）。
    //    → 改为「aside 里存在任意 sc-ui-chevron 元素」即可（收起/展开都成立）。
    var chev = aside ? aside.querySelector('[class*="sc-ui-chevron"]') : null;

    var ginfo = {gi:i, title:(g.textContent||'').replace(/\s+/g,' ').slice(0,90),
                 has_chevron: !!chev};
    var rowEls = g.querySelectorAll('.sc-list-row');
    ginfo.rows = [];
    for (var r=0;r<rowEls.length;r++){
      var row = rowEls[r];
      var t = (row.textContent||'').replace(/\s+/g,' ');
      var sm = t.match(/SKU\s*([A-Za-z0-9\-\+]+)\s*#\s*(\d+)/);
      var idm = t.match(/#(\d+)/);
      var rr = {ri:r, sku: sm?sm[1]:null, mlb: (sm?sm[2]:null)||(idm?idm[1]:null),
                head: t.slice(0,90)};
      var boxes = row.querySelectorAll('.sc-list-collapsible-row__promotion-box');
      rr.campaigns = [];
      for (var b=0;b<boxes.length;b++){
        var bx = boxes[b];
        var bt = (bx.textContent||'').replace(/\s+/g,' ');
        var btns = [];
        bx.querySelectorAll('button').forEach(function(bn){
          var x=(bn.textContent||'').replace(/\s+/g,' ').trim();
          if(x && x.length<12) btns.push(x);   // 只收短文案，过滤掉整块说明文字
        });
        rr.campaigns.push({
          bi:b, txt: bt, btns: btns,
          activated: /激活/.test(bt), scheduled: /已预约/.test(bt)
        });
      }
      ginfo.rows.push(rr);
    }
    out.groups.push(ginfo);
  }
  return JSON.stringify(out);
})();
"""

# 页面元信息：是否空态 / 末页号 / 组数 / 商品总数。
PAGE_META = r"""
(function(){
  var body = document.body ? (document.body.innerText || '') : '';
  var empty = /无法创建促销活动/.test(body);   // 平台越界页 / 无活动时的空态文案

  // ⚠️ 不能用 `[class*="pagination"]`：页面里 communications-carousel__pagination
  //    （轮播图的分页容器，文本为空）在文档顺序上排在列表分页之前，
  //    querySelector 会先命中它 → 读到 0 个页码 → last_page=null → 末页收口失效
  //    → 脚本去请求越界页 → 平台渲染空态 → dump_page 白等 40~60s。
  //    正确的容器是列表自己的 div.sc-list-pagination（内含 nav > ul.andes-pagination）。
  var scopes = ['.sc-list-pagination', '.andes-pagination'];
  var nums = [];
  for (var s = 0; s < scopes.length; s++) {
    var sc = document.querySelector(scopes[s]);
    if (!sc) continue;
    sc.querySelectorAll('[data-andes-pagination-item="true"], li, a').forEach(function(e){
      var t = (e.textContent || '').replace(/\s+/g, '').trim();
      if (/^\d{1,3}$/.test(t)) nums.push(parseInt(t, 10));
      var al = e.getAttribute ? e.getAttribute('aria-label') : null;
      var m = al && al.match(/第\s*(\d+)\s*页/);
      if (m) nums.push(parseInt(m[1], 10));
    });
    if (nums.length) break;
  }
  var last = nums.length ? Math.max.apply(null, nums) : null;
  var im = body.match(/(\d+)\s*条商品信息/);
  return JSON.stringify({
    empty: empty,
    last_page: last,
    items: im ? parseInt(im[1], 10) : null,
    n_groups: document.querySelectorAll('.sc-list-row-group').length
  });
})();
"""

# ============================================================
# 二、组展开
# ============================================================

# 给第 %d 个组的箭头打标记。
# 只打标记不点击——点击必须走 CDP 真实鼠标事件。
EXPAND_ONE = r"""
(function(){
  var g = document.querySelectorAll('.sc-list-row-group')[%d];
  var aside = g.querySelector('.sc-list-row-group__aside');
  var chev = aside ? aside.querySelector('[class*="sc-ui-chevron"]') : null;
  if (!chev) return JSON.stringify({ok:false, err:'no chev'});
  chev.setAttribute('data-ajchev','1');
  return JSON.stringify({ok:true});
})();
"""

# ============================================================
# 三、按钮标记
# ============================================================

# 按「组索引 / 行索引 / 卡片索引」精确定位按钮并打标记。
# 参数顺序：want(文案数组 JSON), gi, ri, bi, attr
TAG_ACTION = r"""
(function(){
  var want = %s;
  var g = document.querySelectorAll('.sc-list-row-group')[%d];
  var row = g.querySelectorAll('.sc-list-row')[%d];
  var boxes = row.querySelectorAll('.sc-list-collapsible-row__promotion-box');
  var bx = boxes[%d];
  if (!bx) return JSON.stringify({ok:false, err:'no box'});
  var btns = bx.querySelectorAll('button');
  for (var k=0;k<btns.length;k++){
    var x=(btns[k].textContent||'').replace(/\s+/g,' ').trim();
    if (want.indexOf(x)>=0){
      btns[k].scrollIntoView({block:'center', inline:'center'});
      btns[k].setAttribute('%s','1');
      return JSON.stringify({ok:true, t:x});
    }
  }
  return JSON.stringify({ok:false, err:'no btn'});
})();
"""

# 兜底：按索引找不到按钮时，在**同一行**里按卡片文本签名重新定位。
#
# 触发场景（实测）：同一链接连续参加多个提案，第一次成功后 DOM 重排
# （新加入的那条变成「已预约/修改」并可能换位），后面几条的 bi 索引全部失效
# → TAG_ACTION 报 no btn。曾经因此在一轮里静默丢掉 3 条。
#
# 参数顺序：want(文案 JSON), sig(签名 JSON), gi, ri, attr
TAG_ACTION_SCAN = r"""
(function(){
  var want = %s;
  var sig  = %s;
  var g = document.querySelectorAll('.sc-list-row-group')[%d];
  if (!g) return JSON.stringify({ok:false, err:'no group'});
  var rows = g.querySelectorAll('.sc-list-row');
  var row = rows[%d];
  if (!row) return JSON.stringify({ok:false, err:'no row'});
  var boxes = row.querySelectorAll('.sc-list-collapsible-row__promotion-box');
  var pick = null, firstAny = null;
  for (var i=0;i<boxes.length;i++){
    var bx = boxes[i];
    var btns = bx.querySelectorAll('button');
    var hit = null;
    for (var k=0;k<btns.length;k++){
      var x = (btns[k].textContent||'').replace(/\s+/g,' ').trim();
      if (want.indexOf(x) >= 0){ hit = btns[k]; break; }
    }
    if (!hit) continue;
    if (!firstAny) firstAny = {i:i, b:hit};
    var t = (bx.textContent||'').replace(/\s+/g,' ');
    if (sig && t.indexOf(sig) >= 0){ pick = {i:i, b:hit}; break; }
  }
  var use = pick || firstAny;
  if (!use) return JSON.stringify({ok:false, err:'no btn(scan)'});
  use.b.scrollIntoView({block:'center', inline:'center'});
  use.b.setAttribute('%s','1');
  return JSON.stringify({ok:true, bi:use.i, t:(use.b.textContent||'').trim(),
                         via: pick ? 'scan_sig' : 'scan_first'});
})();
"""

# 清掉某个标记属性的所有残留。
# ⚠️ 每次打新标记前必须调用。不清的话 CDP 会命中文档里第一个残留标记元素。
CLEAR_ATTR = r"""
(function(){
  var els = document.querySelectorAll('[%s="1"]');
  for (var i=0;i<els.length;i++) els[i].removeAttribute('%s');
  return JSON.stringify({ok:true, n:els.length});
})();
"""

# 「查看更多促销」触发器。实测是纯视觉折叠，默认不点，保留作备选。
TAG_MORE_ROW = r"""
(function(){
  var g = document.querySelectorAll('.sc-list-row-group')[%d];
  var row = g.querySelectorAll('.sc-list-row')[%d];
  var cands = row.querySelectorAll('.sc-list-collapsible-row__trigger');
  for (var i=0;i<cands.length;i++){
    var t=(cands[i].textContent||'').replace(/\s+/g,' ').trim();
    if (t==='查看更多促销'){
      cands[i].scrollIntoView({block:'center', inline:'center'});
      cands[i].setAttribute('data-ajmore','1');
      return JSON.stringify({ok:true});
    }
  }
  return JSON.stringify({ok:false});
})();
"""

# ============================================================
# 四、弹窗与回执
# ============================================================

# 弹窗整体状态：是否打开、是否限时抢购、数量输入框当前值、仓库数量、按钮清单。
READ_MODAL = r"""
(function(){
  var m = document.querySelector('.promotion-modal-cdp2026');
  if (!m) return JSON.stringify({open:false});
  var inp = m.querySelector('.promotion-modal-cdp2026__row--input input');
  var txt = (m.textContent||'').replace(/\s+/g,' ');
  var wm = txt.match(/仓库[：:]\s*(\d+)\s*个/);
  var is_deal = /限时抢购/.test(txt);
  var btns = [];
  m.querySelectorAll('button').forEach(function(b){
    var t=(b.textContent||'').replace(/\s+/g,' ').trim();
    if(t) btns.push({t:t.slice(0,10), disabled:b.disabled});
  });
  return JSON.stringify({open:true, is_deal:is_deal,
      has_input: !!inp, input_value: inp?inp.value:null,
      warehouse: wm?wm[1]:null, btns:btns, txt: txt.slice(0,1200)});
})();
"""

# 【主用】「净收款额」按列结构化读取。
#
# 实测 DOM（优化折扣弹窗）：
#   <div class="sc-multi-column-charges sc-multi-column-charges--cols-3 …">
#     <div class="sc-multi-column-charges__summary">净收款额 查看详情</div>
#     <div>R$ 110,64</div>   ← 第1列 = 当前
#     <div>R$ 93,94</div>    ← 第2列 = 新提案
#     <div>R$ 85,15</div>    ← 第3列 = 可选
#   </div>
#
# → 以「净收款额」叶子为锚，取它父节点的**后续兄弟**，即为各列数值（顺序 = 列顺序）。
#   全弹窗「净收款额」只有 1 处（已实测，不是 visually-hidden 干扰），锚点唯一。
READ_NET_CELLS = r"""
(function(){
  var m = document.querySelector('.promotion-modal-cdp2026');
  if (!m) return JSON.stringify({open:false});
  var sum = null;
  m.querySelectorAll('*').forEach(function(e){
    if (sum) return;
    if (!e.children.length && (e.textContent||'').trim() === '净收款额') sum = e.parentElement;
  });
  if (!sum || !sum.parentElement) return JSON.stringify({open:true, found:false});
  var wrap = sum.parentElement;
  var cells = [];
  Array.prototype.slice.call(wrap.children).forEach(function(ch){
    if (ch === sum) return;
    cells.push((ch.textContent||'').replace(/\s+/g,' ').trim());
  });
  return JSON.stringify({open:true, found:true,
      cols_class: String(wrap.className||''), cells: cells});
})();
"""

# 数量输入框赋值。
# ⚠️ 输入框是 React 受控组件：直接赋 el.value = x 不生效（React 内部 state 不更新，
#    提交时还是旧值）。必须用 nativeSetter 绕过 React 的 value 拦截，
#    再补发 input / change 两个事件让 React 的 onChange 收到变更。
FILL_UNITS = r"""
(function(){
  var m = document.querySelector('.promotion-modal-cdp2026');
  var wrap = m.querySelector('.promotion-modal-cdp2026__row--input');
  var inp = wrap ? wrap.querySelector('input') : null;
  if (!inp) return JSON.stringify({ok:false, err:'no input'});
  var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(inp, '%s');
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  inp.dispatchEvent(new Event('change', {bubbles:true}));
  return JSON.stringify({ok:true, value_now: inp.value});
})();
"""

# 确认按钮在 .andes-modal__actions 里，**不在** modal 根节点内。
# 曾经在 modal 内找「确认」找不到，原因是这个动作区在 DOM 上是兄弟节点。
TAG_CONFIRM = r"""
(function(){
  var m = document.querySelector('.andes-modal__actions');
  if (!m) return JSON.stringify({ok:false, err:'no actions'});
  var btns = m.querySelectorAll('button');
  for (var i=0;i<btns.length;i++){
    if ((btns[i].textContent||'').indexOf('确认')>=0){
      btns[i].setAttribute('data-ajconfirm','1');
      return JSON.stringify({ok:true, disabled: btns[i].disabled});
    }
  }
  return JSON.stringify({ok:false, err:'no confirm'});
})();
"""

TAG_CANCEL = r"""
(function(){
  var m = document.querySelector('.promotion-modal-cdp2026');
  if (!m) return JSON.stringify({ok:false});
  var btns = m.querySelectorAll('button');
  for (var i=0;i<btns.length;i++){
    if ((btns[i].textContent||'').indexOf('取消')>=0){
      btns[i].setAttribute('data-ajcancel','1');
      return JSON.stringify({ok:true});
    }
  }
  return JSON.stringify({ok:false});
})();
"""

# 回读某张卡片操作后的状态。
# ⚠️ 仅作记录，**不作为成败判据**——索引可能移位，读到的未必是刚操作的那张。
VERIFY_BOX = r"""
(function(){
  var g = document.querySelectorAll('.sc-list-row-group')[%d];
  var row = g.querySelectorAll('.sc-list-row')[%d];
  var boxes = row.querySelectorAll('.sc-list-collapsible-row__promotion-box');
  var bx = boxes[%d];
  var btns = [];
  bx.querySelectorAll('button').forEach(function(b){
    var t=(b.textContent||'').replace(/\s+/g,' ').trim();
    if(t) btns.push(t.slice(0,10));
  });
  var t = (bx.textContent||'').replace(/\s+/g,' ');
  return JSON.stringify({btns:btns, scheduled:/已预约/.test(t), activated:/激活/.test(t),
                         still_join: btns.indexOf('立即参与')>=0 || btns.indexOf('优化折扣')>=0});
})();
"""

# snackbar 回执。成败判据的**唯一**来源。
READ_SNACKBAR = r"""
(function(){
  var s = document.querySelector('.andes-snackbar, [class*="snackbar"]');
  return JSON.stringify({snackbar: s ? (s.textContent||'').replace(/\s+/g,' ').slice(0,120) : null});
})();
"""
