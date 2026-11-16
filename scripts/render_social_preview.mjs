// Renders assets/social-preview.png (1280x640) for the GitHub "Social preview" setting.
//   node scripts/render_social_preview.mjs
import { chromium } from '../web/node_modules/@playwright/test/index.mjs'
import { writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const html = `<!doctype html><meta charset=utf-8><style>
*{box-sizing:border-box;margin:0}
body{width:1280px;height:640px;background:#090c12;color:#e8ecf4;font-family:-apple-system,"Inter","Segoe UI",sans-serif;overflow:hidden;position:relative}
.logo{position:absolute;left:72px;top:64px;height:64px}
p.lede{position:absolute;left:72px;top:276px;width:500px;font-size:20px;line-height:1.4;color:#aeb6c8;z-index:2}
h1{position:absolute;left:72px;top:160px;width:540px;font-size:40px;line-height:1.16;font-weight:650;letter-spacing:-.02em}
ul{position:absolute;left:72px;top:416px;list-style:none;padding:0;width:500px}
li{font-size:19px;color:#8f9ab0;padding:9px 0 9px 22px;border-top:1px solid #1d2535;position:relative}
li:before{content:"";position:absolute;left:0;top:19px;width:8px;height:8px;background:#5b8def}
li b{color:#e8ecf4;font-weight:600}
.shot{position:absolute;left:640px;top:96px;width:740px;border:1px solid #2a3550;box-shadow:0 20px 60px rgba(0,0,0,.6)}
.logo,h1,ul,.url{z-index:2}
.fade{z-index:1;position:absolute;inset:0;background:linear-gradient(90deg,#090c12 0,#090c12 46%,rgba(9,12,18,0) 56%)}
.url{position:absolute;left:72px;bottom:40px;font:15px ui-monospace,Menlo,monospace;color:#5d6882}
</style>
<img class=logo src="file://${ROOT}/assets/logo/proef-lockup-onDark.png">
<h1>Open-source framework for agentic science</h1>
<p class=lede>AI agents propose hypotheses, run experiments and judge the results against criteria fixed before the experiment ran.</p>
<ul><li><b>Abstains</b> when the 95% interval straddles the gate</li><li><b>Tamper-evident</b> record of every decision</li><li><b>A person approves</b> what reaches the lab</li></ul>
<img class=shot src="file://${ROOT}/assets/screenshots/research-result.png">
<div class=fade></div><div class=url>github.com/rbalachandar/proef</div>`
writeFileSync('/tmp/social.html', html)
const b = await chromium.launch({ channel: 'msedge' })
const p = await b.newPage({ viewport: { width: 1280, height: 640 } })
await p.goto('file:///tmp/social.html')
await p.waitForTimeout(300)
await p.screenshot({ path: join(ROOT, 'assets/social-preview.png') })
await b.close()
