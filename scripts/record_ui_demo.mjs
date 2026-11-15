#!/usr/bin/env node
/**
 * Records the workbench demo GIF: define a problem, let a model propose, review the plan, run, decide, verify.
 * It needs a REAL model configured on the server (the plan is the point) and a SAMPLE workspace with a simulated lab:
 *
 *   python scripts/seed_sample_workspace.py .sample-rec
 *   cd .sample-rec && PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna PROEF_DATA_DIR=.data \
 *       ../.venv/bin/uvicorn proef.api.app:app --port 8038
 *   node scripts/record_ui_demo.mjs http://localhost:8038
 *
 * Needs web/node_modules (Playwright) and ffmpeg. Output: assets/demo/proef-ui-demo.gif.
 * The waits on the model and the campaign are real time, so they are cut to a short fast-forward when encoding.
 */
import { chromium } from '../web/node_modules/@playwright/test/index.mjs'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.argv[2] || 'http://localhost:8038'
const OUT = join(ROOT, 'assets/demo/proef-ui-demo.gif')
const dir = mkdtempSync(join(tmpdir(), 'proef-rec-'))
const SPEED = 1.25        // playback speed of the normal scenes
const FAST = 9            // playback speed of the waits on the model and the campaign

const b = await chromium.launch({ channel: 'msedge' })
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, recordVideo: { dir, size: { width: 1280, height: 800 } } })
await ctx.addInitScript(() => {
  const mk = () => {
    if (document.getElementById('__cur')) return
    const s = document.createElement('style')
    s.textContent = `#__cur{position:fixed;z-index:99999;width:18px;height:18px;border-radius:50%;background:rgba(43,79,196,.9);border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.4);pointer-events:none;transform:translate(-50%,-50%);left:-50px;top:-50px;transition:transform .08s}
    #__cur.d{transform:translate(-50%,-50%) scale(.7)}
    #__cap{position:fixed;z-index:99998;left:50%;bottom:22px;transform:translateX(-50%);background:rgba(11,16,32,.92);color:#eef1f8;font:500 15px Inter,system-ui,sans-serif;padding:9px 18px;border-radius:2px;pointer-events:none;box-shadow:0 4px 18px rgba(0,0,0,.3);max-width:86vw;white-space:nowrap}`
    document.head.appendChild(s)
    const c = document.createElement('div'); c.id = '__cur'; document.body.appendChild(c)
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px' }, true)
    addEventListener('mousedown', () => c.classList.add('d'), true); addEventListener('mouseup', () => c.classList.remove('d'), true)
  }
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', mk) : mk()
})
const p = await ctx.newPage()
const T0 = Date.now()
const now = () => (Date.now() - T0) / 1000
const marks = []          // {s, e}: spans that are fast-forwarded
const fast = async (fn) => { const s = now(); await fn(); marks.push({ s, e: now() }) }
const wait = (ms) => p.waitForTimeout(ms)
const caption = (t) => p.evaluate((t) => { let c = document.getElementById('__cap'); if (!c) { c = document.createElement('div'); c.id = '__cap'; document.body.appendChild(c) } c.textContent = t }, t)
const move = async (loc) => { await loc.scrollIntoViewIfNeeded(); const bb = await loc.boundingBox(); await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2, { steps: 20 }) }
const click = async (loc, after = 700) => { await move(loc); await wait(160); await loc.click(); await wait(after) }
const type = async (loc, text) => { await move(loc); await loc.click(); await loc.pressSequentially(text, { delay: 24 }); await wait(500) }
const scrollMain = async (dy, times, gap = 450) => { await p.mouse.move(760, 460); for (let i = 0; i < times; i++) { await p.mouse.wheel(0, dy); await wait(gap) } }

// ---- 1. a program is a goal with a gate fixed before any data
await p.goto(BASE + '/?theme=light'); await wait(1400)
await p.mouse.move(640, 400)
await caption('Programs: each is a goal with a gate fixed before any data'); await wait(2200)

await caption('Define the problem')
await click(p.getByRole('button', { name: /New program/ }), 800)
await type(p.getByPlaceholder('EGFR hit finding'), 'EGFR follow-on')
await type(p.getByPlaceholder(/Find a small molecule that inhibits EGFR/), 'Find a small molecule that inhibits EGFR below 20 µM, confirmed by an orthogonal assay.')
await caption('The gate is fixed first: IC50 at or below 20 µM'); await wait(2000)
await click(p.locator('details summary'), 1400)
await caption('Hard limits cap every campaign: here 2 families of 2 variants')
await p.getByLabel('Families', { exact: true }).fill('2'); await wait(500)
await p.getByLabel('Variants per family').fill('2'); await wait(1800)
await click(p.getByRole('button', { name: 'Create program' }), 1500)

// ---- 2. a model proposes, a person decides
await caption('Start research: a model proposes, a person decides')
await click(p.getByRole('button', { name: 'Start research' }).first(), 1200)
const replan = p.getByLabel(/Re-plan after each round/)
if (await replan.count()) await replan.uncheck()
await wait(900)
await click(p.getByRole('dialog').getByRole('button', { name: 'Start research' }), 900)
await caption('The model proposes a plan: families of hypotheses, each with a refutation condition')
await fast(async () => {
  await click(p.getByRole('link', { name: 'decisions' }).first(), 500)
  await p.waitForSelector('text=Review the research plan', { timeout: 240000 })
})
await wait(1200)
await caption('Nothing reaches the lab until a person decides'); await wait(2600)
await scrollMain(300, 2, 700)
await caption('A second model critiques the plan. It is advisory only'); await wait(2400)
await scrollMain(300, 3, 700)
await caption('Remove what is not worth a lab run, with a note for the record')
const boxes = p.getByLabel(/^Test /)
const n = await boxes.count()
await boxes.nth(n - 1).uncheck(); await wait(900)
await type(p.getByPlaceholder(/Note for the record/), 'near-duplicate of an earlier variant')
await click(p.getByRole('button', { name: /^Approve/ }), 1200)

// ---- 3. the agent runs it, a fixed rule judges
await caption('The agent runs it. The verdicts come from a fixed rule')
await fast(async () => {
  for (let i = 0; i < 90; i++) {
    const list = await (await fetch(`${BASE}/api/v1/campaigns`)).json()
    const mine = list.find((c) => /EGFR below 20/.test(c.title) && c.program?.name === 'EGFR follow-on')
    if (mine && mine.status === 'completed') break
    await wait(4000)
  }
})
await click(p.getByRole('link', { name: 'campaigns' }).first(), 900)
await click(p.getByRole('link', { name: /Find a small molecule that inhibits EGFR below 20/ }).first(), 2200)
await caption('Validated, refuted, or abstained, each against the gate registered first'); await wait(3000)

const decide = p.getByRole('button', { name: 'Decide' })
if (await decide.count()) {
  await caption('An abstention is not a failure. A person decides what is next')
  await click(decide.first(), 1800)
  await click(p.getByRole('button', { name: /Accept as inconclusive/ }).first(), 1500)
  await caption('The decision is recorded in the ledger under a name'); await wait(1800)
  await p.keyboard.press('Escape'); await wait(500)
}

await caption('Rounds: what was tested, and why')
await click(p.getByRole('tab', { name: 'rounds' }), 2400)
await caption('Record: who did what, every action sealed in the ledger')
await click(p.getByRole('tab', { name: 'record' }), 1200)
await click(p.getByRole('button', { name: /^campaign/ }).first(), 2400)

// ---- 4. control and trust
await caption('Pause a program: new lab submissions are held, running plates finish')
await click(p.getByRole('link', { name: 'Programs' }).first(), 900)
await click(p.getByRole('link', { name: /EGFR follow-on/ }).first(), 1200)
await click(p.getByRole('button', { name: 'Pause', exact: true }), 700)
await type(p.getByPlaceholder(/Reason/), 'reviewing the follow-up plan')
await click(p.getByRole('button', { name: 'Pause program' }), 2200)
await click(p.getByRole('button', { name: /Resume/ }), 3300)

await click(p.getByRole('link', { name: 'Evidence' }).first(), 900)
await caption('Evidence: verify the tamper-evident ledger at any time')
await click(p.getByRole('button', { name: /Verify now/ }), 1000)
await p.waitForSelector('text=Intact'); await wait(2400)
await caption('Light and dark')
await click(p.getByRole('button', { name: 'Toggle theme' }), 1800)

const total = now()
await ctx.close(); await b.close()
const webm = join(dir, readdirSync(dir).find((f) => f.endsWith('.webm')))

// ---- encode: normal scenes at SPEED, the waits at FAST
const cuts = []
let at = 0
for (const m of marks) { if (m.s > at) cuts.push({ s: at, e: m.s, v: SPEED }); cuts.push({ s: m.s, e: m.e, v: FAST }); at = m.e }
cuts.push({ s: at, e: total + 5, v: SPEED })
const parts = cuts.map((c, i) => `[0:v]trim=start=${c.s.toFixed(2)}:end=${c.e.toFixed(2)},setpts=(PTS-STARTPTS)/${c.v}[v${i}]`)
const concat = cuts.map((_, i) => `[v${i}]`).join('') + `concat=n=${cuts.length}:v=1:a=0[v]`
const tail = `[v]fps=6,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=40:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle`
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-filter_complex', [...parts, concat, tail].join(';'), '-loop', '0', OUT])
console.log('wrote', OUT, (statSync(OUT).size / 1e6).toFixed(2), 'MB;', 'fast-forwarded', marks.map((m) => `${m.s.toFixed(0)}-${m.e.toFixed(0)}s`).join(', '))
