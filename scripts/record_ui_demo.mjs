#!/usr/bin/env node
/**
 * Records the workbench demo GIF from a SAMPLE workspace (simulated lab), never a real one.
 *
 *   make sample PORT=8020            # or:  python scripts/seed_sample_workspace.py .sample-demo
 *   node scripts/record_ui_demo.mjs  [http://localhost:8020]
 *
 * Needs web/node_modules (Playwright) and ffmpeg. Output: assets/demo/proef-ui-demo.gif
 */
import { chromium } from '../web/node_modules/@playwright/test/index.mjs'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.argv[2] || 'http://localhost:8020'
const PLATE = join(ROOT, 'assets/demo/demo-plate.csv')
const OUT = join(ROOT, 'assets/demo/proef-ui-demo.gif')
const dir = mkdtempSync(join(tmpdir(), 'proef-rec-'))

const b = await chromium.launch({ channel: 'msedge' })
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, recordVideo: { dir, size: { width: 1280, height: 800 } } })
await ctx.addInitScript(() => {
  const mk = () => {
    if (document.getElementById('__cur')) return
    const s = document.createElement('style')
    s.textContent = `#__cur{position:fixed;z-index:99999;width:18px;height:18px;border-radius:50%;background:rgba(255,138,31,.9);border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.4);pointer-events:none;transform:translate(-50%,-50%);left:-50px;top:-50px;transition:transform .08s}
    #__cur.d{transform:translate(-50%,-50%) scale(.7)}
    #__cap{position:fixed;z-index:99998;left:50%;bottom:22px;transform:translateX(-50%);background:rgba(11,16,32,.92);color:#eef1f8;font:500 15px Inter,system-ui,sans-serif;padding:9px 18px;border-radius:999px;pointer-events:none;box-shadow:0 4px 18px rgba(0,0,0,.3);max-width:80vw;white-space:nowrap}`
    document.head.appendChild(s)
    const c = document.createElement('div'); c.id = '__cur'; document.body.appendChild(c)
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px' }, true)
    addEventListener('mousedown', () => c.classList.add('d'), true); addEventListener('mouseup', () => c.classList.remove('d'), true)
  }
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', mk) : mk()
})
const p = await ctx.newPage()
const wait = (ms) => p.waitForTimeout(ms)
const caption = (t) => p.evaluate((t) => { let c = document.getElementById('__cap'); if (!c) { c = document.createElement('div'); c.id = '__cap'; document.body.appendChild(c) } c.textContent = t }, t)
const move = async (loc) => { await loc.scrollIntoViewIfNeeded(); const bb = await loc.boundingBox(); await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2, { steps: 22 }) }
const click = async (loc, after = 700) => { await move(loc); await wait(180); await loc.click(); await wait(after) }

await p.goto(BASE + '/?theme=light'); await wait(1500)
await p.mouse.move(640, 400)

await caption('Programs: each is judged against a gate fixed before any data'); await wait(2600)

await caption('A campaign: every estimate against the gate, with its 95% interval')
await click(p.getByRole('link', { name: 'Campaigns' }).first(), 900)
await click(p.getByRole('link', { name: /Find a confirmed sub-20/ }).first(), 2400)
await caption('Three candidates abstained: the interval crosses the gate, so no claim is made')
await wait(2600)

await click(p.getByRole('button', { name: /EG-04/ }).first(), 900)
await caption('Open one: the evidence is re-read from the recorded plate')
await click(p.getByRole('tab', { name: 'Evidence' }), 3000)
await p.keyboard.press('Escape'); await wait(600)

await caption('Rounds: what was tested, and why')
await click(p.getByRole('tab', { name: 'rounds' }), 2200)
await caption('Record: who did what, every action sealed in the ledger')
await click(p.getByRole('tab', { name: 'record' }), 1200)
await click(p.getByRole('button', { name: /^campaign/ }).first(), 2600)

await caption('Pause a program: new lab submissions are held, running plates finish')
await click(p.getByRole('link', { name: 'Programs' }).first(), 1000)
await click(p.getByRole('link', { name: /EGFR hit finding/ }).first(), 1200)
await click(p.getByRole('button', { name: 'Pause', exact: true }), 700)
await p.getByPlaceholder(/Reason/).fill('reviewing the follow-up plan'); await wait(700)
await click(p.getByRole('button', { name: 'Pause program' }), 2400)
await click(p.getByRole('button', { name: /Resume/ }), 1400); await wait(3600)   // let the toasts clear before the next scene

await caption('Now run it yourself: the gate is registered before the plate is read')
await click(p.getByRole('button', { name: /Candidate list/ }).first(), 700)
await click(p.getByRole('radio', { name: /Manual plate run/ }), 400)
await p.getByPlaceholder(/EG-02/).fill('C1\nBORD'); await wait(900)
await click(p.getByRole('button', { name: 'Create campaign' }), 2200)

await caption('Submit the plate: it is judged by those pre-registered criteria, and sealed first')
await click(p.getByRole('button', { name: /Submit results/ }), 600)
await p.locator('input[type=file]').setInputFiles(PLATE)
await p.waitForSelector('text=Will be judged'); await wait(2800)
await click(p.getByRole('button', { name: /Submit and judge/ }), 1200)
await p.waitForSelector('text=Recorded in the ledger'); await wait(2200)
await click(p.getByRole('button', { name: 'Done' }), 900)
await caption('C1 clears the gate, BORD abstains. Every step is on the record')
await wait(3400)

await click(p.getByRole('link', { name: 'Evidence' }).first(), 900)
await caption('Evidence: verify the tamper-evident ledger at any time')
await click(p.getByRole('button', { name: /Verify now/ }), 1000)
await p.waitForSelector('text=Intact'); await wait(2600)

await caption('Light and dark')
await click(p.getByRole('button', { name: 'Toggle theme' }), 2000)

const video = p.video()
await ctx.close(); await b.close()
const webm = join(dir, readdirSync(dir).find((f) => f.endsWith('.webm')))
const SPEED = 1.1   // playback speed of the final GIF
const vf = `setpts=PTS/${SPEED},fps=6,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=40:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle`
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf', vf, '-loop', '0', OUT])
console.log('wrote', OUT, (statSync(OUT).size / 1e6).toFixed(2), 'MB')
