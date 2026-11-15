/* Shared by the three wireframes: mean + 95% interval per candidate against the gate. Shapes AND colour carry state. */
const C = { validated: '#2b4fc4', refuted: '#c8321e', abstained: '#b8860b', proposed: '#8a8f9c' }
const DATA = [
  { id: 'LPSCl-ClRich', fam: 'argyrodite', est: 2.04, lo: 1.71, hi: 2.37, st: 'validated', n: 3 },
  { id: 'Li3N-textured-fast', fam: 'nitride', est: 1.60, lo: 0.62, hi: 2.58, st: 'abstained', n: 3 },
  { id: 'Li3N-textured-dense', fam: 'nitride', est: 4.43, lo: 0.31, hi: 8.55, st: 'abstained', n: 3 },
  { id: 'LLZO-Nb05-dense', fam: 'garnet', est: 0.57, lo: 0.12, hi: 1.02, st: 'abstained', n: 3 },
  { id: 'LLZO-Ta06-dense', fam: 'garnet', est: 0.067, lo: 0.031, hi: 0.103, st: 'refuted', n: 3 },
  { id: 'LPSClBr-mixed', fam: 'argyrodite', est: null, lo: null, hi: null, st: 'proposed', n: 0 },
]
const GATE = 1
let MARKER_FILL = '#fff'
function marker(x, y, st, r = 6) {
  const c = C[st]
  if (st === 'refuted') return `<rect x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" fill="${c}"/>`
  if (st === 'abstained') return `<path d="M${x},${y - r - 2} L${x + r + 2},${y} L${x},${y + r + 2} L${x - r - 2},${y} Z" fill="${MARKER_FILL}" stroke="${c}" stroke-width="2.2"/>`
  if (st === 'proposed') return `<circle cx="${x}" cy="${y}" r="${r - 2}" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="2 2"/>`
  return `<circle cx="${x}" cy="${y}" r="${r}" fill="${c}"/>`
}
/** opts: width, rowH, left (label gutter), labels: 'inline'|'none', ink, line, font, goodFill, axisLabel */
function gatePlot(el, o) {
  if (o.markerFill) MARKER_FILL = o.markerFill
  const W = o.width, rowH = o.rowH || 46, left = o.left || 0, pad = 16, axisH = 34, top = o.top || 0
  const dom = [0.03, 12], lx = (v) => Math.log10(v), x0 = left + pad, x1 = W - pad
  const x = (v) => x0 + ((lx(v) - lx(dom[0])) / (lx(dom[1]) - lx(dom[0]))) * (x1 - x0)
  const H = top + DATA.length * rowH + axisH, ink = o.ink || '#151a2c', line = o.line || 'rgba(21,26,44,.16)'
  let s = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Estimates with 95% intervals against the gate at 1 mS/cm" style="font-family:${o.font || 'inherit'}">`
  s += `<rect x="${x(GATE)}" y="${top}" width="${x1 - x(GATE)}" height="${DATA.length * rowH}" fill="${o.goodFill || 'rgba(43,79,196,.07)'}"/>`
  DATA.forEach((d, i) => {
    const y = top + i * rowH + rowH / 2
    s += `<line x1="${o.full ? 0 : x0}" x2="${W}" y1="${top + (i + 1) * rowH}" y2="${top + (i + 1) * rowH}" stroke="${line}"/>`
    if (d.lo != null) s += `<line x1="${x(d.lo)}" x2="${x(d.hi)}" y1="${y}" y2="${y}" stroke="${C[d.st]}" stroke-width="2.4" stroke-linecap="round"/><line x1="${x(d.lo)}" x2="${x(d.lo)}" y1="${y - 6}" y2="${y + 6}" stroke="${C[d.st]}" stroke-width="2"/><line x1="${x(d.hi)}" x2="${x(d.hi)}" y1="${y - 6}" y2="${y + 6}" stroke="${C[d.st]}" stroke-width="2"/>`
    if (d.est != null) s += marker(x(d.est), y, d.st)
    else s += marker(x(0.4), y, 'proposed') + `<text x="${x(0.4) + 12}" y="${y + 4}" font-size="11" fill="${o.faint || '#7b8190'}">queued for round 3</text>`
    if (o.labels === 'inline') s += `<text x="0" y="${y + 4}" font-size="13" fill="${ink}" font-family="${o.mono || 'ui-monospace,Menlo,monospace'}">${d.id}</text>`
  })
  s += `<line x1="${x(GATE)}" x2="${x(GATE)}" y1="${top - 2}" y2="${top + DATA.length * rowH}" stroke="${ink}" stroke-width="1.5"/>`
  s += `<text x="${x(GATE) + 6}" y="${top + 11}" font-size="11" fill="${ink}" font-family="${o.mono || 'ui-monospace,Menlo,monospace'}">gate ≥ 1</text>`
  const ay = top + DATA.length * rowH
  s += `<line x1="${x0}" x2="${x1}" y1="${ay}" y2="${ay}" stroke="${ink}" stroke-opacity=".5"/>`
  ;[0.1, 1, 10].forEach((t) => { s += `<line x1="${x(t)}" x2="${x(t)}" y1="${ay}" y2="${ay + 5}" stroke="${ink}" stroke-opacity=".5"/><text x="${x(t)}" y="${ay + 18}" font-size="11" text-anchor="middle" fill="${o.mute || '#5b6172'}" font-family="${o.mono || 'ui-monospace,Menlo,monospace'}">${t}</text>` })
  s += `<text x="${x1}" y="${ay + 31}" font-size="11" text-anchor="end" fill="${o.mute || '#5b6172'}">conductivity, mS/cm (log scale)</text></svg>`
  el.innerHTML = s
}
const fmt = (v) => (v == null ? '·' : v >= 1 ? v.toFixed(2) : v.toFixed(v < 0.1 ? 3 : 2))
