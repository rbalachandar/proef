const THEME = (new URLSearchParams(location.search).get('theme') || document.documentElement.dataset.force) === 'light' ? 'light' : 'dark'
document.documentElement.dataset.theme = THEME
if (THEME === 'light') Object.assign(C, { validated: '#2b4fc4', refuted: '#c8321e', abstained: '#6b7385', proposed: '#8590a8' })
else Object.assign(C, { validated: '#5b8def', refuted: '#e8604c', abstained: '#aeb6c8', proposed: '#5d6882' })
const PLOT = THEME === 'light'
  ? { ink: '#0f1626', line: 'rgba(15,22,38,.09)', goodFill: 'rgba(43,79,196,.07)', markerFill: '#ffffff', mute: '#545f78', faint: '#8590a8' }
  : { ink: '#e8ecf4', line: 'rgba(255,255,255,.07)', goodFill: 'rgba(91,141,239,.10)', markerFill: '#0e131c', mute: '#8f9ab0', faint: '#5d6882' }
/* small status marks: shape carries state as well as colour */
const MK = {
  validated: `<svg class="mk" width="11" height="11"><circle cx="5.5" cy="5.5" r="5" fill="${C.validated}"/></svg>`,
  refuted: `<svg class="mk" width="11" height="11"><rect x=".5" y=".5" width="10" height="10" fill="${C.refuted}"/></svg>`,
  abstained: `<svg class="mk" width="12" height="12"><path d="M6 1 L11 6 L6 11 L1 6 Z" fill="none" stroke="${C.abstained}" stroke-width="1.8"/></svg>`,
  achieved: `<svg class="mk" width="12" height="12"><circle cx="6" cy="6" r="5.2" fill="${C.validated}"/></svg>`,
  partial: `<svg class="mk" width="12" height="12"><circle cx="6" cy="6" r="5" fill="none" stroke="${C.validated}" stroke-width="1.6"/><path d="M6 1 A5 5 0 0 1 6 11 Z" fill="${C.validated}"/></svg>`,
  failed: `<svg class="mk" width="11" height="11"><rect x=".5" y=".5" width="10" height="10" fill="${C.refuted}"/></svg>`,
  inconclusive: `<svg class="mk" width="12" height="12"><path d="M6 1 L11 6 L6 11 L1 6 Z" fill="none" stroke="${C.abstained}" stroke-width="1.8"/></svg>`,
  lab: `<svg class="mk" width="12" height="12"><circle cx="6" cy="6" r="5" fill="none" stroke="${C.validated}" stroke-width="1.5" stroke-dasharray="2.4 2"/><circle cx="6" cy="6" r="1.8" fill="${C.validated}"/></svg>`,
  open: `<svg class="mk" width="12" height="12"><circle cx="6" cy="6" r="4.6" fill="none" stroke="${C.proposed}" stroke-width="1.4" stroke-dasharray="1.6 2.2"/></svg>`,
}
document.querySelectorAll('[data-mk]').forEach((e) => { e.insertAdjacentHTML('afterbegin', MK[e.dataset.mk]) })
