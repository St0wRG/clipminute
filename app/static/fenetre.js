/* ClipMinute — barre de titre maison, active UNIQUEMENT dans la fenêtre native
   (pywebview frameless). Dans un navigateur classique : ne fait rien. */
(function () {
  function installer() {
    if (document.getElementById("cm-titlebar")) return;
    var st = document.createElement("style");
    st.textContent =
      "#cm-titlebar{position:fixed;top:0;left:0;right:0;height:36px;z-index:99999;" +
      "display:flex;align-items:stretch;background:#0b0e13;border-bottom:1px solid #232c3a;user-select:none}" +
      "#cm-titlebar .t{display:flex;align-items:center;gap:8px;padding:0 14px;" +
      "font:700 12.5px 'Segoe UI',system-ui,sans-serif;color:#eaeef4;letter-spacing:.02em}" +
      "#cm-titlebar .t .dot{width:7px;height:7px;border-radius:50%;background:#b6ff3a;box-shadow:0 0 8px #b6ff3a}" +
      "#cm-titlebar .drag{flex:1}" +
      "#cm-titlebar .b{display:flex;align-items:stretch}" +
      "#cm-titlebar .b button{all:unset;width:46px;display:flex;align-items:center;justify-content:center;" +
      "cursor:pointer;color:#8b96a6;font-size:13px;transition:background .12s,color .12s}" +
      "#cm-titlebar .b button:hover{background:#1a2230;color:#eaeef4}" +
      "#cm-titlebar .b button.x:hover{background:#e81123;color:#fff}" +
      "body{padding-top:36px !important}" +
      /* barre de défilement discrète, assortie au thème (fini la barre Windows) */
      "::-webkit-scrollbar{width:11px;height:11px}" +
      "::-webkit-scrollbar-track{background:transparent}" +
      "::-webkit-scrollbar-thumb{background:#232c3a;border-radius:8px;border:3px solid #0b0e13}" +
      "::-webkit-scrollbar-thumb:hover{background:#3a4a66}" +
      "::-webkit-scrollbar-corner{background:transparent}";
    document.head.appendChild(st);

    var tb = document.createElement("div");
    tb.id = "cm-titlebar";
    tb.innerHTML =
      '<div class="t pywebview-drag-region"><span class="dot"></span>ClipMinute</div>' +
      '<div class="drag pywebview-drag-region"></div>' +
      '<div class="b"><button id="cm-min" title="Réduire">─</button>' +
      '<button id="cm-max" title="Agrandir / restaurer">▢</button>' +
      '<button id="cm-x" class="x" title="Fermer">✕</button></div>';
    document.body.appendChild(tb);

    document.getElementById("cm-min").addEventListener("click", function () { window.pywebview.api.reduire(); });
    document.getElementById("cm-max").addEventListener("click", function () { window.pywebview.api.agrandir(); });
    document.getElementById("cm-x").addEventListener("click", function () { window.pywebview.api.fermer(); });
  }
  if (window.pywebview) { installer(); }
  else { window.addEventListener("pywebviewready", installer); }
})();
