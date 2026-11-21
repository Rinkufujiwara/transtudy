// A2UI-style cards: draws a card the server sent (already checked against the catalog in app/cards.py).
// Text goes in with textContent only. Buttons send { action, ... } to POST /api/cards/{card_id}/action;
// the server decides what that means from what it stored, not from this page.
//
// Usage:  TranstudyCards.show(card, frameEl, { onDone(result) {} })
//         TranstudyCards.close()
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const PATHS = {
    check: ["M20 6 9 17l-5-5"],
    bot: ["M12 8V4H8", "M2 14h2", "M20 14h2", "M15 13v2", "M9 13v2"],
    plus: ["M5 12h14", "M12 5v14"],
    minus: ["M5 12h14"],
  };

  function icon(name, size) {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", size || 16);
    s.setAttribute("height", size || 16);
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    if (name === "bot") {
      const r = document.createElementNS(SVG, "rect");
      r.setAttribute("x", "4"); r.setAttribute("y", "8"); r.setAttribute("width", "16");
      r.setAttribute("height", "12"); r.setAttribute("rx", "2");
      s.appendChild(r);
    }
    for (const d of PATHS[name] || []) {
      const p = document.createElementNS(SVG, "path");
      p.setAttribute("d", d);
      s.appendChild(p);
    }
    return s;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  const KIND_LABEL = { word: "Word", expression: "Expression", grammar: "Grammar" };
  let current = null;

  function close() {
    if (!current) return;
    const { sheet, backdrop } = current;
    current = null;
    sheet.classList.remove("open");
    backdrop.classList.remove("open");
    setTimeout(() => { sheet.remove(); backdrop.remove(); }, 320);
  }

  async function send(card, body) {
    const res = await fetch(`/api/cards/${encodeURIComponent(card.card_id)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* keep empty */ }
    if (!res.ok) throw new Error(data.detail || "Could not reach the app.");
    return data;
  }

  function show(card, frame, opts) {
    close();
    opts = opts || {};
    const backdrop = el("div", "a2ui-backdrop");
    const sheet = el("section", "a2ui-sheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-label", card.title);

    const head = el("div", "a2ui-head");
    const who = el("span", "a2ui-who");
    who.appendChild(icon("bot", 14));
    who.appendChild(document.createTextNode(card.agent === "capture_agent" ? "Capture Agent" : "Tutor Agent"));
    head.appendChild(who);
    sheet.appendChild(head);
    sheet.appendChild(el("h3", "a2ui-title", card.title));
    if (card.why) sheet.appendChild(el("p", "a2ui-why", card.why));

    const list = el("div", "a2ui-items");
    const selected = new Set();
    const primary = { btn: null };

    function refreshPrimary() {
      if (card.card !== "extract" || !primary.btn) return;
      const n = selected.size;
      primary.btn.textContent = n ? `Save ${n} to wordbook` : "Choose what to save";
      primary.btn.disabled = n === 0;
    }

    for (const it of card.items) {
      if (card.card === "meaning") {      // one tap answers: the meaning the person looked up
        const b = el("button", "a2ui-choice", it.text);
        b.type = "button";
        b.addEventListener("click", () => act({ action: "choose_meaning", choice: it.id }));
        list.appendChild(b);
        continue;
      }
      const row = el(card.card === "extract" ? "button" : "div", "a2ui-item");
      if (it.role) row.classList.add("role-" + it.role);
      if (card.card === "extract") {
        row.type = "button";
        row.setAttribute("role", "checkbox");
        const box = el("span", "a2ui-box");
        box.appendChild(icon("check", 14));
        row.appendChild(box);
        const set = (on) => {
          on ? selected.add(it.id) : selected.delete(it.id);
          row.setAttribute("aria-checked", on ? "true" : "false");
          row.classList.toggle("on", on);
          refreshPrimary();
        };
        set(!!it.checked);
        row.addEventListener("click", () => set(!selected.has(it.id)));
      } else if (it.role) {
        const mark = el("span", "a2ui-mark");
        mark.appendChild(icon(it.role === "add" ? "plus" : "minus", 14));
        row.appendChild(mark);
      }
      const body = el("span", "a2ui-body");
      const top = el("span", "a2ui-line");
      top.appendChild(el("span", "a2ui-text", it.text));
      if (it.kind) top.appendChild(el("span", "a2ui-pill", KIND_LABEL[it.kind] || it.kind));
      if (it.known) top.appendChild(el("span", "a2ui-pill known", "In your wordbook"));
      body.appendChild(top);
      if (it.note) body.appendChild(el("span", "a2ui-note", it.note));
      row.appendChild(body);
      list.appendChild(row);
    }
    sheet.appendChild(list);

    // "Drop another word": the person picks which word leaves today's review instead.
    const choices = el("div", "a2ui-choices");
    choices.hidden = true;
    for (const c of card.choices || []) {
      const b = el("button", "a2ui-choice", c.text);
      b.type = "button";
      b.addEventListener("click", () => act({ action: "choose_drop", drop_entry_id: c.id }));
      choices.appendChild(b);
    }
    sheet.appendChild(choices);

    const status = el("p", "a2ui-status");
    status.setAttribute("aria-live", "polite");
    const actions = el("div", "a2ui-actions");
    for (const b of card.buttons) {
      const btn = el("button", "a2ui-btn" + (b.style === "primary" ? " primary" : ""), b.label);
      btn.type = "button";
      if (b.style === "primary") primary.btn = btn;
      btn.addEventListener("click", () => {
        if (b.action === "choose_drop") {
          choices.hidden = !choices.hidden;
          list.hidden = !choices.hidden;
          if (primary.btn) primary.btn.hidden = !choices.hidden;   // while choosing, tapping a word is the answer
          btn.textContent = choices.hidden ? b.label : "Back";
          return;
        }
        if (b.action === "save_selected") return act({ action: b.action, selected: [...selected] });
        act({ action: b.action });
      });
      actions.appendChild(btn);
    }
    sheet.appendChild(status);
    sheet.appendChild(actions);

    let busy = false;
    async function act(body) {
      if (busy) return;
      busy = true;
      sheet.querySelectorAll("button").forEach((b) => (b.disabled = true));
      status.textContent = "";
      try {
        const result = await send(card, body);
        status.textContent = result.message || "Done";
        if (opts.onDone) opts.onDone(result);
        setTimeout(close, body.action === "dismiss" ? 0 : 900);
      } catch (e) {
        status.textContent = e.message;
        busy = false;
        sheet.querySelectorAll("button").forEach((b) => (b.disabled = false));
        refreshPrimary();
      }
    }

    backdrop.addEventListener("click", () => act({ action: "dismiss" }));
    frame.appendChild(backdrop);
    frame.appendChild(sheet);
    refreshPrimary();
    current = { sheet, backdrop };
    sheet.getBoundingClientRect();   // settle the closed position, then animate open (no rAF: works in background tabs too)
    sheet.classList.add("open");
    backdrop.classList.add("open");
  }

  window.TranstudyCards = { show, close };
})();
