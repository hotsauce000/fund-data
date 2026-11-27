/* fund-data 文档站 · 行为层
   只做一件事：函数索引的即时筛选（纯原生，零依赖，零网络）。
   标记契约（build_docs.py 生成）：[data-fd-filter] 容器；[data-fd-item] 条目（值 = 可检索文本）；
   [data-fd-in] 条目所属分组；[data-fd-group] 分组标题（值 = 分组号）；[data-fd-count] 计数；
   [data-fd-empty] 无结果提示。
   Material 开启 navigation.instant 后，文档节点会被整体替换，
   所以订阅 document$ 在每次换页后重新绑定（同一容器只绑一次）。 */
(function () {
  "use strict";

  function initFilter(box) {
    if (box.dataset.fdBound) return;
    box.dataset.fdBound = "1";

    var input = box.querySelector("input[type=search]");
    var items = Array.prototype.slice.call(box.querySelectorAll("[data-fd-item]"));
    var groups = Array.prototype.slice.call(box.querySelectorAll("[data-fd-group]"));
    var count = box.querySelector("[data-fd-count]");
    var empty = box.querySelector("[data-fd-empty]");
    var emptyQuery = empty ? empty.querySelector("[data-fd-empty-query]") : null;
    var total = items.length;
    if (!input || !total) return;
    var composing = false;                  // 中文输入法组字期间不筛选（否则打拼音会闪一下「无结果」）

    function apply() {
      var query = input.value.trim().toLowerCase();
      var terms = query ? query.split(/\s+/) : [];    // 多个关键词取交集
      var shown = 0;
      var visibleInGroup = {};

      items.forEach(function (li) {
        var text = li.dataset.fdItem;
        var hit = terms.every(function (t) { return text.toLowerCase().indexOf(t) !== -1; });
        li.hidden = !hit;                   // 隐藏靠 hidden 属性：CSS 里有对应规则压过条目自己的 display
        if (hit) {
          shown += 1;
          visibleInGroup[li.dataset.fdIn] = true;
        }
      });

      groups.forEach(function (g) {
        g.hidden = !visibleInGroup[g.dataset.fdGroup];
      });

      if (count) count.textContent = query ? shown + " / " + total : total;
      if (empty) {
        empty.hidden = shown !== 0;
        if (emptyQuery) emptyQuery.textContent = query;
      }
    }

    input.addEventListener("input", function () {
      if (!composing) apply();
    });
    input.addEventListener("compositionstart", function () { composing = true; });
    input.addEventListener("compositionend", function () { composing = false; apply(); });
    apply();
  }

  function init(root) {
    var boxes = root.querySelectorAll("[data-fd-filter]");
    Array.prototype.forEach.call(boxes, initFilter);
  }

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(function () {
      init(document);
    });
  } else {
    init(document);
  }
})();

/* ── 预览用：配色切换条（定稿后本段与 extra.css 第 9 节一并删除）──
   在 body 上挂 data-fd-accent，选择记在 localStorage；body 在 instant 导航中不重建，
   所以只需挂一次。朱砂为默认方案（不挂属性）。 */
(function () {
  "use strict";

  var THEMES = [
    ["vermilion", "朱砂", "#b5432e"],
    ["ink", "松烟", "#2a2622"],
    ["moss", "苔绿", "#3d6b4f"],
    ["ochre", "赭石", "#8e5a26"],
  ];
  var KEY = "fd-accent";

  function current() {
    return document.body.getAttribute("data-fd-accent") || "vermilion";
  }

  function sync() {
    Array.prototype.forEach.call(document.querySelectorAll(".fd-themes button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.fdAccent === current()));
    });
  }

  function apply(id) {
    if (id && id !== "vermilion") {
      document.body.setAttribute("data-fd-accent", id);
    } else {
      document.body.removeAttribute("data-fd-accent");
    }
    try {
      if (id && id !== "vermilion") {
        localStorage.setItem(KEY, id);
      } else {
        localStorage.removeItem(KEY);
      }
    } catch (e) {
      /* 隐私模式下 localStorage 不可用：仅本次有效 */
    }
    sync();
  }

  if (!document.querySelector(".fd-themes")) {
    var saved = null;
    try {
      saved = localStorage.getItem(KEY);
    } catch (e) {
      saved = null;
    }
    var known = THEMES.filter(function (t) {
      return t[0] === saved;
    });
    apply(known.length ? saved : null);

    var bar = document.createElement("div");
    bar.className = "fd-themes";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "配色方案（预览）");
    THEMES.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.fdAccent = t[0];
      b.style.setProperty("--sw", t[2]);
      b.textContent = t[1];
      b.insertBefore(document.createElement("i"), b.firstChild);
      b.addEventListener("click", function () {
        apply(t[0]);
      });
      bar.appendChild(b);
    });
    document.body.appendChild(bar);
    sync();
  }
})();
