/*
 * Quota Strip card for Home Assistant.
 *
 * Draws an account the way the strip draws its panels (quota_display.py, compact
 * 613 x 414 style, scaled to the card's width), from the MQTT sensors published
 * by quota_ha.py. It makes no requests of its own. See docs/HOME_ASSISTANT.md.
 *
 *   type: custom:quota-strip-card
 *   account: claude                      # one panel; or
 *   accounts: [claude, claude2, codex]   # a row of panels, like the strip
 *
 * An account entry may also be {id: claude2, title: WORK} to change its title.
 *
 * The logos are assets/claude.svg and assets/chatgpt.svg (Lobe Icons; see
 * assets/README.md and assets/LICENSE.lobe-icons for their source and license).
 */
const VERSION = "1.0.0";

// Colors from quota_display.py.
const C = {
  panel: "#121921", track: "#27313b", fg: "#e7eef2", muted: "#8e9eaa", blue: "#78cdef",
  purple: "#c29df5", green: "#94d5a0", red: "#f87372", orange: "#eeab67", yellow: "#ead280",
};
// The compact panel's geometry in strip pixels (quota_display.COMPACT, panel top at 0).
const W = 613, H = 414, PAD = 28;
const ROWS = { two: [130, 110], three: [130, 86] };   // first row y, row gap
const FONT = '"DejaVu Sans", "Bitstream Vera Sans", Verdana, sans-serif';
// Claude limit-reset kinds in Claude Code's order: header name, note name.
const KINDS = [["session", "5H", "5-hour"], ["weekly", "WEEKLY", "Weekly"],
               ["fable", "FABLE", "Fable"], ["full", "FULL", "Full"]];
const LOGOS = {
  claude: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z" fill="#D97757" fill-rule="nonzero"></path></svg>`,
  codex: `<svg fill="currentColor" fill-rule="evenodd" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 00-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 01.476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303l-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 014.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 01-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 005.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0010.205 0a5.947 5.947 0 00-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 004.162 1.713z"></path></svg>`,
};
const UNSET = new Set(["unavailable", "unknown", "", undefined, null]);

const esc = (value) => String(value).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rounded = (x) => Math.floor(x + 0.5);                  // quota_model.rounded
const halfEven = (x) => {                                     // Python's f"{x:.0f}"
  const f = Math.floor(x), d = x - f;
  return d > 0.5 ? f + 1 : d < 0.5 ? f : (f % 2 === 0 ? f : f + 1);
};
const rateColor = (used) => used >= 95 ? C.red : used >= 75 ? C.orange : used >= 50 ? C.yellow : C.green;

function countdown(reset, now) {                             // quota_model.countdown
  if (reset === null) return "Reset time unavailable";
  if (reset <= now) return "Reset reached - awaiting update";
  let minutes = Math.floor((reset - now) / 60000);
  if (minutes < 1) return "Resets in <1m";
  const days = Math.floor(minutes / 1440); minutes -= days * 1440;
  const hours = Math.floor(minutes / 60); minutes -= hours * 60;
  return "Resets in " + (days ? `${days}d ` : "") + (hours ? `${hours}h ` : "") + `${minutes}m`;
}

class QuotaStripCard extends HTMLElement {
  setConfig(config) {
    const list = config.accounts ?? (config.account ? [config.account] : null);
    if (list !== null && (!Array.isArray(list) || !list.length)) {
      throw new Error("Set account: <id> or accounts: [<id>, ...]");
    }
    this.accounts = list && list.map((a) => typeof a === "string" ? { id: a } : a);
    for (const a of this.accounts || []) {
      if (!a || typeof a.id !== "string" || !/^[a-z0-9_]+$/.test(a.id)) throw new Error(`Invalid account ${JSON.stringify(a)}`);
    }
    this.config = config;
    this.signature = null;
    if (this._hass) this.render();
  }

  set hass(hass) {
    this._hass = hass;
    const ids = this.accountIds();
    const prefixes = ids.map((id) => `sensor.quota_strip_${id}_`);
    let signature = "";
    for (const [entity, state] of Object.entries(hass.states)) {
      if (prefixes.some((p) => entity.startsWith(p))) signature += entity + state.last_updated;
    }
    if (signature !== this.signature) {
      this.signature = signature;
      this.render();
    }
  }

  connectedCallback() {
    // Countdowns, ages and overflow rotation move without new states, as on the strip.
    this.timer = setInterval(() => this._hass && this.render(), 15000);
    this.resize = new ResizeObserver(() => this._hass && this.render());
    this.resize.observe(this);
  }

  disconnectedCallback() {
    clearInterval(this.timer);
    this.resize?.disconnect();
  }

  getCardSize() { return 5 * this.accountIds().length; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }
  static getStubConfig(hass) {
    const id = Object.keys(hass.states).map((e) => e.match(/^sensor\.quota_strip_(.+)_banked_resets$/))
      .find(Boolean);
    return { account: id ? id[1] : "claude" };
  }

  accountIds() {
    if (this.accounts) return this.accounts.map((a) => a.id);
    const found = new Set();
    for (const entity of Object.keys(this._hass?.states || {})) {
      const m = entity.match(/^sensor\.quota_strip_(.+)_(plan|banked_resets)$/);
      if (m) found.add(m[1]);
    }
    return [...found].sort((a, b) => (a.startsWith("codex") - b.startsWith("codex")) || a.localeCompare(b));
  }

  state(entity) {
    const s = this._hass.states[entity];
    return s && !UNSET.has(s.state) ? s : null;
  }

  number(entity) {
    const s = this.state(entity);
    const value = s ? Number(s.state) : NaN;
    return Number.isFinite(value) ? value : null;
  }

  time(entity) {
    const s = this.state(entity);
    const value = s ? Date.parse(s.state) : NaN;
    return Number.isFinite(value) ? value : null;
  }

  date(ms, separator = " ") {                       // "%d %b %H:%M" in Home Assistant's zone
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
      timeZone: this._hass.config.time_zone, day: "2-digit", month: "short",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(ms)).map((p) => [p.type, p.value]));
    return `${parts.day} ${parts.month}${separator}${parts.hour}:${parts.minute}`;
  }

  textWidth(text, px, bold) {
    this.canvas ??= document.createElement("canvas").getContext("2d");
    this.canvas.font = `${bold ? "bold " : ""}${px}px ${FONT}`;
    return this.canvas.measureText(text).width;
  }

  // The account's quota windows that have a current reading, in the strip's order.
  windows(id, name) {
    const out = [];
    const prefix = `sensor.quota_strip_${id}_`;
    for (const entity of Object.keys(this._hass.states)) {
      if (!entity.startsWith(prefix) || !entity.endsWith("_used")) continue;
      const base = entity.slice(0, -"_used".length);
      const used = this.number(entity);
      if (used === null) continue;                  // Not reported now, or no fresh reading.
      let label = (this._hass.states[entity].attributes.friendly_name || "")
        .replace(/^Quota Strip /i, "").replace(/ Used$/i, "");
      if (label.toLowerCase().startsWith(name.toLowerCase() + " ")) label = label.slice(name.length + 1);
      if (!label) label = base.slice(prefix.length).replace(/_/g, " ");
      const seconds = /weekly$/i.test(label) ? 604800 : /5-hour$/i.test(label) ? 18000 : null;
      out.push({ entity, label, used, seconds, fable: base.endsWith("_fable_weekly"),
                 budget: this.number(base + "_pacing_allowance"), reset: this.time(base + "_reset") });
    }
    const rank = (w) => w.seconds === 18000 && /^5-hour$/i.test(w.label) ? 0 : /^weekly$/i.test(w.label) ? 1 : 2;
    return out.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
  }

  bar(width, used, budget, unavailable, accent, segments, s) {   // Display.bar, in strip pixels × s
    const h = 14 * s, gap = 3 * s, radius = 4 * s;
    const fill = width * Math.max(0, Math.min(100, used)) / 100;
    const color = unavailable ? C.muted : budget !== null ? accent : rateColor(used);
    const mark = budget !== null ? width * budget / 100 : null;
    const over = mark !== null && !unavailable && used > budget;
    const uid = Math.random().toString(36).slice(2);
    let body = "";
    for (let i = 0; i < segments; i++) {
      // Six-pixel gaps centred on time boundaries keep the percentage scale and marker aligned.
      const left = Math.round(width * i / segments) + (i ? gap : 0);
      const right = Math.round(width * (i + 1) / segments) - (i < segments - 1 ? gap : 0);
      body += `<rect x="${left}" y="0" width="${right - left}" height="${h}" rx="${radius}" fill="${C.track}"/>`;
      const filled = Math.min(right, fill) - left;        // segment.clip(fill)
      if (filled > 0) {
        const r = Math.min(radius, filled / 2);
        body += `<rect x="${left}" y="0" width="${filled}" height="${h}" rx="${r}" fill="${color}"/>`;
        if (over) body += `<rect x="${left}" y="0" width="${filled}" height="${h}" rx="${r}" fill="${C.red}" clip-path="url(#o${uid})"/>`;
      }
    }
    if (mark !== null && !unavailable) {
      const x = Math.min(width - 1.5 * s, Math.max(1.5 * s, mark));
      body += `<line x1="${x}" y1="${-5 * s}" x2="${x}" y2="${h + 5 * s}" stroke="${over ? C.red : C.green}" stroke-width="${3 * s}"/>`;
    }
    return `<svg class="bar" width="${width}" height="${h + 10 * s}" viewBox="0 ${-5 * s} ${width} ${h + 10 * s}"
      style="top:${-5 * s}px"><defs><clipPath id="o${uid}"><rect x="${mark ?? 0}" y="${-1}" width="${Math.max(0, width - (mark ?? 0))}" height="${h + 2}"/></clipPath></defs>${body}</svg>`;
  }

  bank(id, provider, stale, inner, now, s) {             // Display.reset_bank
    const noun = provider === "codex" ? "BANKED RESET" : "LIMIT RESET";
    const count = this.number(`sensor.quota_strip_${id}_banked_resets`);
    const top = (html) => `<div class="t" style="left:${PAD * s}px;top:${70 * s}px;font-size:${16 * s}px;font-weight:bold;max-width:${inner}px">${html}</div>`;
    const note = (text, color) => `<div class="t" style="left:${PAD * s}px;top:${92 * s}px;font-size:${14 * s}px;color:${color};max-width:${inner}px">${esc(text)}</div>`;
    if (count === null) {
      return top(`<span style="color:${C.muted}">${noun}S —</span>`) + note("Count unavailable", C.muted);
    }
    const expiryState = this._hass.states[`sensor.quota_strip_${id}_banked_reset_expiry`];
    const expiry = this.time(`sensor.quota_strip_${id}_banked_reset_expiry`);
    const complete = expiryState?.attributes?.details_complete ?? provider === "claude";
    const old = stale || (expiry !== null && expiry <= now);
    let color = old || !count ? C.muted : C.green;
    const plural = count !== 1 ? "S" : "";
    const split = this.state(`sensor.quota_strip_${id}_banked_resets`)?.attributes?.kinds;
    const kinds = split && typeof split === "object"
      ? KINDS.filter(([k]) => split[k]).map(([k, big, small]) => ({ kind: k, big, small, ...split[k] })) : [];
    const parts = kinds.flatMap((k, i) => [...(i ? [[" · ", color]] : []),
      [`${k.count} ${k.big}`, old || k.kind !== "fable" ? color : C.purple]]).concat([[` RESET${plural}`, color]]);
    const fits = kinds.length && parts.reduce((w, [t]) => w + this.textWidth(t, 16 * s, true), 0) <= inner;
    const header = fits ? parts.map(([t, c]) => `<span style="color:${c}">${esc(t)}</span>`).join("")
      : `<span style="color:${color}">${count} ${noun}${plural}</span>`;
    const single = () => expiry === null ? (complete ? "No expiry" : "Expiry unavailable")
      : `${complete ? "Next expires" : "Listed expiry"} ${this.date(expiry, " · ")}`;
    let text;
    if (old) text = "Last known · awaiting update";
    else if (count === 0) text = "None available";
    else if (kinds.length > 1) {
      text = kinds.map((k) => `${k.small} ` + (k.expiry ? this.date(Date.parse(k.expiry)) : "no expiry")).join(" · ");
      if (this.textWidth(text, 14 * s, false) > inner) text = single();
    } else text = single();
    if (!old && count && expiry !== null && expiry - now <= 86400000) color = C.orange;
    return top(header) + note(text, color);
  }

  row(w, y, inner, now, stale, s) {                       // Display.window
    const expired = w.reset !== null && w.reset <= now;
    const budget = stale ? null : w.budget;
    const unavailable = stale || expired;
    const accent = w.fable ? C.purple : w.seconds === 18000 ? C.green : C.blue;
    let status = unavailable ? C.muted : rateColor(w.used);
    if (budget !== null && !unavailable) status = w.used <= budget ? C.green : C.red;
    const value = `${rounded(w.used)}%` + (budget !== null ? ` / ${rounded(budget)}%` : "");
    let note;
    if (expired) note = "Awaiting fresh quota";
    else if (budget !== null) {
      const delta = budget - w.used, size = halfEven(Math.abs(delta));
      note = w.seconds === 18000
        ? (delta >= 0 ? `${size}% left now` : `${size}% over current budget`)
        : (delta >= 0 ? `${size}% left today` : `${size}% over today's budget`);
    } else note = `${halfEven(Math.max(0, 100 - w.used))}% remaining`;
    const segments = { 604800: 7, 18000: 5 }[w.seconds] || 1;
    const x = PAD * s;
    return `<div class="row" data-entity="${esc(w.entity)}">
      <div class="t" style="left:${x}px;top:${(y + 3) * s}px;font-size:${17 * s}px;font-weight:bold;color:${w.fable ? C.purple : C.muted};max-width:${Math.max(100 * s, inner - 260 * s)}px">${esc(w.label.toUpperCase())}</div>
      <div class="t r" style="right:${x}px;top:${(y - 4) * s}px;font-size:${30 * s}px;font-weight:bold;color:${status}">${esc(value)}</div>
      <div class="b" style="left:${x}px;top:${(y + 36) * s}px;width:${inner}px;height:${14 * s}px">${this.bar(inner, w.used, budget, unavailable, accent, segments, s)}</div>
      <div class="t" style="left:${x}px;top:${(y + 58) * s}px;font-size:${15 * s}px;color:${C.muted};max-width:${inner / 2}px">${esc(countdown(w.reset, now))}</div>
      <div class="t r" style="right:${x}px;top:${(y + 58) * s}px;font-size:${15 * s}px;color:${status};max-width:${inner / 2 - 10 * s}px">${esc(note)}</div>
    </div>`;
  }

  panel(account, width, now) {                            // Display.panel
    const id = account.id;
    const name = id.charAt(0).toUpperCase() + id.slice(1);   // The bridge names entities "<Id> …".
    const status = this._hass.states[`sensor.quota_strip_${id}_plan`];
    const attrs = status && status.state !== "unavailable" ? status.attributes : null;
    const provider = attrs?.provider || (id.startsWith("codex") ? "codex" : "claude");
    const s = Math.min(1.25, Math.max(0.6, width / W));
    const inner = width - 2 * PAD * s;
    const all = this.windows(id, name);
    const observed = attrs?.observed_at ? Date.parse(attrs.observed_at)
      : Math.max(0, ...all.map((w) => Date.parse(this._hass.states[w.entity].attributes.observed_at) || 0)) || null;
    const stale = attrs ? attrs.fresh === false : !all.length;
    const plan = status && !UNSET.has(status.state) ? status.state : "";
    let html = `
      <div class="t logo ${provider}" style="left:${(PAD - 4) * s}px;top:${16 * s}px;width:${40 * s}px;height:${40 * s}px">${LOGOS[provider]}</div>
      <div class="t" style="left:${78 * s}px;top:${16 * s}px;font-size:${28 * s}px;font-weight:bold;color:${C.fg}">${esc(account.title || id.toUpperCase())}</div>
      ${plan ? `<div class="t r" style="right:${22 * s}px;top:${24 * s}px;font-size:${17 * s}px;color:${C.muted}">${esc(plan)}</div>` : ""}
      ${this.bank(id, provider, stale, inner, now, s)}`;
    let windows = all;
    if (!windows.length) {
      const age = observed ? `Last reading ${Math.max(0, Math.floor((now - observed) / 60000))}m ago` : "No reading from the strip";
      html += `<div class="t" style="left:${PAD * s}px;top:${139 * s}px;font-size:${30 * s}px;font-weight:bold;color:${C.fg}">${stale && observed ? "No fresh quota" : "Waiting for quota"}</div>
        <div class="t" style="left:${PAD * s}px;top:${190 * s}px;font-size:${19 * s}px;color:${C.muted};max-width:${inner}px">${esc(age)}</div>`;
    } else {
      if (windows.length > 3) {                            // Keep two; rotate the rest in slot three.
        const slot = Math.floor(now / 15000) % (windows.length - 2);
        windows = [windows[0], windows[1], windows[2 + slot]];
        html += `<div class="t r" style="right:${22 * s}px;top:${92 * s}px;font-size:${14 * s}px;color:${C.muted}">More limits ${slot + 1}/${all.length - 2}</div>`;
      }
      const [start, gap] = windows.length > 2 ? ROWS.three : ROWS.two;
      windows.forEach((w, i) => { html += this.row(w, start + i * gap, inner, now, stale, s); });
    }
    if (observed) {
      const warning = attrs?.warning;
      let text = `${stale ? "STALE" : warning ? "PARTIAL" : "UPDATED"} ${Math.max(0, Math.floor((now - observed) / 60000))}m ago`;
      if (attrs?.source) text += ` · ${attrs.source}`;
      if (warning) text += ` · ${warning}`;
      html += `<div class="t" style="left:${PAD * s}px;top:${387 * s}px;font-size:${14 * s}px;color:${stale || warning ? C.orange : C.muted};max-width:${inner}px">${esc(text)}</div>`;
    }
    return `<div class="panel" style="height:${H * s}px;border-radius:${14 * s}px">${html}</div>`;
  }

  render() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (event) => {
        const row = event.composedPath().find((el) => el.dataset?.entity);
        if (!row) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: row.dataset.entity }, bubbles: true, composed: true }));
      });
    }
    const accounts = this.accounts || this.accountIds().map((id) => ({ id }));
    const now = Date.now();
    const width = this.clientWidth || W;
    const columns = Math.max(1, Math.min(accounts.length, Math.floor((width + 20) / (W * 0.6 + 20))));
    const panelWidth = (width - 20 * (columns - 1)) / columns;
    this.shadowRoot.innerHTML = `<style>
        :host { display: block; }
        .grid { display: grid; grid-template-columns: repeat(${columns}, 1fr); gap: 20px; }
        .panel { position: relative; overflow: hidden; background: ${C.panel}; color: ${C.fg};
                 font-family: ${FONT}; line-height: normal; }
        .t { position: absolute; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .t.r { text-align: right; }
        .b { position: absolute; }
        .bar { position: absolute; left: 0; overflow: visible; }
        .logo svg { width: 100%; height: 100%; display: block; }
        .logo.codex { color: #fff; }
        .row { cursor: pointer; }
      </style>
      <div class="grid">${accounts.length ? accounts.map((a) => this.panel(a, panelWidth, now)).join("")
        : `<div class="panel" style="height:${H * 0.6}px">${"<div class='t' style='left:17px;top:17px'>No Quota Strip sensors found</div>"}</div>`}</div>`;
  }
}

customElements.define("quota-strip-card", QuotaStripCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "quota-strip-card", name: "Quota Strip",
  description: "Claude and Codex quotas drawn like the Quota Strip's panels." });
console.info(`%c QUOTA-STRIP-CARD %c ${VERSION} `, "color:#121921;background:#78cdef", "color:#e7eef2;background:#121921");
