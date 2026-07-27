#!/usr/bin/env node
/**
 * Generate a plain-language Chinese PDF that explains the SHA reconstruction
 * work. The images are embedded so the PDF can be shared as a single file.
 */

import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const root = process.cwd();
const outputDir = path.join(root, "output", "pdf");
const outputPath = path.join(outputDir, "管道SHA图纸还原过程总结.pdf");

const imagePaths = {
  firstPrototype: "output/sha_svg/N400P3A-AMSS2-N444201-01-page-1-sha-prototype.svg.png",
  lineStage: "output/sha_svg/N400P3A-AMSS2-N444201-01-page-1-sha-full-lines.svg.png",
  directStage: "output/sha_svg/N400P3A-AMSS2-N444201-01-page-1-direct-sha.svg.png",
  rightDetail: "output/sha_svg/right-detail-direct.png",
  titleDetail: "output/sha_svg/title-detail-direct.png",
  leftCallouts: "output/sha_svg/left-callouts-direct.png",
  comparison: "output/pdf_visual_diff/current-side-by-side/N400P3A-CHW-N491163-01-p1.png",
  reconstructed: "output/sha_base_iso/lineweight-current/N400P3A-CHW-N491163-01/N400P3A-CHW-N491163-01-0-Sheet6-sha.png",
  welded: "output/sha_pcf_weld_iso/sha-pcf-matched-20260727/N400P3A-CHW-N491163-01/rendered/N400P3A-CHW-N491163-01-welds-Sheet6-sha.png",
  complex: "output/pdf_visual_diff/current-side-by-side/N400P3A-AMSS2-N444201-01-p4.png",
  fullPage: "output/pdf_visual_diff/current-side-by-side/N400P3A-UA-N495128-01-p3.png",
};

async function imageDataUri(relativePath) {
  const absolutePath = path.join(root, relativePath);
  const bytes = await readFile(absolutePath);
  return `data:image/png;base64,${bytes.toString("base64")}`;
}

function page(title, body, extraClass = "") {
  return `<section class="page ${extraClass}">
    <div class="topline"></div>
    ${title ? `<h1>${title}</h1>` : ""}
    ${body}
    <footer>管道 SHA 图纸还原过程总结 <span>2026-07-27</span></footer>
  </section>`;
}

const images = Object.fromEntries(
  await Promise.all(Object.entries(imagePaths).map(async ([name, file]) => [name, await imageDataUri(file)])),
);

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<style>
  @page { size: A4 landscape; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; color: #17202a; font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
  .page { width: 297mm; height: 210mm; padding: 13mm 16mm 12mm; position: relative; overflow: hidden; break-after: page; background: #fbfcfa; }
  .page:last-child { break-after: auto; }
  .topline { height: 2.1mm; width: 35mm; background: #146c72; margin-bottom: 4mm; }
  h1 { margin: 0 0 5mm; font-size: 25px; letter-spacing: .4px; color: #093f43; }
  h2 { margin: 0 0 3mm; font-size: 16px; color: #0b5e63; }
  p { margin: 0 0 3mm; font-size: 12px; line-height: 1.65; }
  .lead { max-width: 190mm; font-size: 17px; line-height: 1.7; }
  .sub { color: #4f5c61; font-size: 12px; }
  .cover { background: linear-gradient(135deg, #e6f2ed 0%, #fbfcfa 54%, #e8f1f2 100%); }
  .cover h1 { margin-top: 33mm; max-width: 175mm; font-size: 39px; line-height: 1.22; }
  .cover .tag { display: inline-block; padding: 2mm 4mm; background: #d3e7e2; color: #0b5e63; font-size: 12px; border-radius: 4mm; margin-bottom: 7mm; }
  .cover .statement { max-width: 165mm; font-size: 16px; line-height: 1.8; }
  .cover .note { position: absolute; right: 18mm; bottom: 18mm; width: 67mm; color: #5e6c70; font-size: 10px; line-height: 1.65; }
  footer { position: absolute; left: 16mm; right: 16mm; bottom: 5mm; border-top: .3mm solid #cfd9d7; padding-top: 2mm; display:flex; justify-content:space-between; color:#607173; font-size:8px; }
  .three { display:grid; grid-template-columns: repeat(3, 1fr); gap: 5mm; }
  .card { background:white; border: .35mm solid #d5e1de; border-radius: 2.5mm; padding: 4.5mm; min-height: 73mm; }
  .card h2 { font-size: 19px; }
  .card .role { color:#6f5a26; font-weight:700; }
  .simple-list { margin: 3mm 0 0; padding: 0; list-style: none; }
  .simple-list li { padding: 1.8mm 0 1.8mm 7mm; position:relative; font-size: 11px; line-height:1.45; }
  .simple-list li:before { content:""; position:absolute; left:0; top: 3.8mm; width:3mm; height:3mm; border-radius:50%; background:#58a69b; }
  .flow { display:flex; align-items:stretch; gap:2mm; margin-top:7mm; }
  .flow .step { flex:1; min-height: 75mm; background:white; border: .35mm solid #cbdcd8; border-radius:2mm; padding:4mm; }
  .flow .number { font-size: 26px; color:#2c8d83; font-weight:700; line-height:1; }
  .flow .arrow { align-self:center; color:#4b918a; font-size:22px; }
  .flow h2 { margin-top:2mm; font-size:14px; }
  .image-frame { background:white; border:.35mm solid #d4dfdc; padding:2.5mm; border-radius:2mm; }
  .image-frame img { display:block; width:100%; height:auto; max-height:119mm; object-fit:contain; }
  .caption { margin-top:2mm; padding-left:3mm; border-left:1mm solid #5a9b92; font-size:10px; line-height:1.45; color:#3c4d50; }
  .two-col { display:grid; grid-template-columns: 1.05fr .95fr; gap:6mm; align-items:start; }
  .two-col .image-frame img { max-height:125mm; }
  .callout { margin:3mm 0; padding:3.3mm 4mm; background:#edf5f2; border-left:1.2mm solid #3d9086; font-size:11px; line-height:1.55; }
  .mini-table { width:100%; border-collapse:collapse; margin-top:3mm; background:white; }
  .mini-table th, .mini-table td { border:.3mm solid #d2dfdc; text-align:left; padding:2.5mm 3mm; font-size:10.3px; line-height:1.35; vertical-align:top; }
  .mini-table th { background:#e5f0ed; color:#164c4f; }
  .highlight { color:#0d686c; font-weight:700; }
  .result-grid { display:grid; grid-template-columns: 1fr 1fr; gap:4mm; }
  .result { padding:4mm; min-height:35mm; border-radius:2mm; background:white; border:.35mm solid #d4e1de; }
  .result strong { display:block; margin-bottom:1mm; color:#0d6367; font-size:16px; }
  .small { font-size:10px; }
  .timeline { margin-top: 4mm; border-left: 1mm solid #4b9b90; padding-left: 6mm; }
  .timeline-item { position: relative; padding: 0 0 5mm 4mm; }
  .timeline-item:before { content:""; position:absolute; left:-8.2mm; top:1mm; width:3.5mm; height:3.5mm; border-radius:50%; background:#146c72; }
  .timeline-item strong { color:#0d6267; font-size:12px; }
  .image-row { display:grid; grid-template-columns:repeat(3, 1fr); gap:4mm; }
  .image-row .image-frame { padding:2mm; }
  .image-row img { width:100%; max-height:93mm; object-fit:contain; display:block; }
  .architecture { display:grid; grid-template-columns: 1fr 1fr; gap:3mm 5mm; }
  .architecture .result { min-height: 26mm; padding:3mm 4mm; }
  .tree { margin:3mm 0; padding:4mm 5mm; border-radius:2mm; background:#183b40; color:#e9f5f1; font-family:"Courier New", monospace; font-size:10px; line-height:1.55; white-space:pre-wrap; }
  .roadmap { display:grid; grid-template-columns:repeat(4, 1fr); gap:4mm; margin-top:4mm; }
  .roadmap .step { background:#fff; border:.35mm solid #d2e1dd; border-top:1.3mm solid #227f78; padding:3.5mm; min-height:95mm; }
  .roadmap .step strong { display:block; color:#0b5d62; font-size:14px; margin-bottom:2mm; }
  .case-page .two-col .image-frame img { max-height: 78mm; }
  .case-page .mini-table th, .case-page .mini-table td { padding: 1.7mm 2.2mm; font-size: 9.2px; }
  .source-page .flow .step { min-height: 65mm; }
  .source-page .mini-table th, .source-page .mini-table td { padding: 1.6mm 2.4mm; font-size: 9.2px; }
</style>
</head>
<body>
${page("", `
  <div class="cover">
    <div class="tag">过程说明 / 可分享版本</div>
    <h1>从 PCF + SHA 到<br>可编辑管道单线图</h1>
    <p class="statement">这份说明不讨论复杂的文件格式。它只讲一件事：我们怎样从已有的工程文件中，一步步把管道单线图重新画出来，并让焊缝编号、图框、材料表、文字标记逐渐接近原来的出图效果。</p>
    <p class="note">核心原则：PDF 只用来检查结果好不好看；真正用于还原和修改的，是 SHA 和 PCF 文件本身。</p>
  </div>
`, "cover")}

${page("先把三份文件说清楚", `
  <p class="lead">可以把这项工作理解成：PCF 告诉我们“管道是什么、连到哪里”；SHA 告诉我们“它在图纸上画在哪里、文字放在哪里”；PDF 是已经出好的样张，用来检查我们画得像不像。</p>
  <div class="three">
    <div class="card"><h2>PCF</h2><p class="role">工程内容清单</p><p>里面有管子、弯头、法兰、阀门等构件的连接关系和三维坐标，也能带焊口编号。</p><ul class="simple-list"><li>擅长回答：这条管线由什么组成？</li><li>擅长回答：焊口 S001 在哪个连接关系上？</li><li>不足：不知道文字该放在哪个纸面位置。</li></ul></div>
    <div class="card"><h2>SHA</h2><p class="role">实际出图的“画面信息”</p><p>里面保存了图框、管线双线、虚线、黑点、构件轮廓、文字、引线、表格等二维内容。</p><ul class="simple-list"><li>擅长回答：这个标记画在图纸哪个位置？</li><li>擅长回答：文字、方框和材料表怎样排版？</li><li>不足：有些底层图元含义需要逐步解码。</li></ul></div>
    <div class="card"><h2>PDF</h2><p class="role">已经画好的对照样张</p><p>它让我们发现偏移、缺字、构件缺线等问题，但不会把 PDF 的坐标或文字反向抄回去。</p><ul class="simple-list"><li>只用于视觉验收。</li><li>用于判断“哪里不一样”。</li><li>修正时仍回到 SHA / PCF 找原因。</li></ul></div>
  </div>
`) }

${page("整个优化过程是怎样推进的", `
  <p class="sub">不是一次性“把文件转成图片”，而是每看到一种问题，就回到 SHA 中寻找对应的原始图元和规律。</p>
  <div class="flow">
    <div class="step"><div class="number">1</div><h2>先按真实页拆开</h2><p>同一条管线可能出成多页。先识别每一张物理图纸，避免只解析 SHA 的第一张图。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">2</div><h2>恢复“看得见的骨架”</h2><p>从 SHA 还原管线、双线、虚线、流向箭头、尺寸、黑点、法兰和常见构件。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">3</div><h2>补齐文字和版面</h2><p>逐步处理注释、方框、引线、图框、标题栏、材料表、中文字体和仪表符号。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">4</div><h2>用 PDF 查问题</h2><p>将原 PDF 与 SHA 还原图并排看。发现偏移后，只回 SHA / PCF 中修改算法。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">5</div><h2>把焊口号接回去</h2><p>把 PCF 的焊口编号和 SHA 中对应的黑点关联，再写入新的 SHA 副本并出图。</p></div>
  </div>
  <div class="callout"><strong>这套方法的意义：</strong>每一步都有来源。不是在图片上手工描线，因此后续可以继续自动化、复用和审计。</div>
`) }

${page("项目从什么时候开始，迭代了多少次", `
  <p class="lead">本轮工作从 <span class="highlight">2026-07-25 23:03</span> 的第一张 SHA 原型图开始。工作目录中保留了至少 <span class="highlight">13 个有文件名的阶段版本</span>，另外还有标题、材料表、注释、焊口布局等局部反复校正版本。</p>
  <div class="timeline">
    <div class="timeline-item"><strong>7 月 25 日 23:03：第一个原型</strong><p>能读出页面范围、少量文字和框线，但材料表文字重叠，主单线图不完整，构件细节基本没有。</p></div>
    <div class="timeline-item"><strong>23:08 至 23:46：连续原型 v2 - v6</strong><p>先解决页面坐标、文字定位、框线和图框。这个阶段的目标只是让“整张纸能出现”。</p></div>
    <div class="timeline-item"><strong>7 月 26 日凌晨：线条与局部版面</strong><p>加入完整线段、双线、尺寸、注释框和右侧区域的局部修正，开始能看清管线走向。</p></div>
    <div class="timeline-item"><strong>7 月 26 日白天：全页还原与多页验证</strong><p>处理图框、材料表、右下角标题栏、中文、法兰/仪表等；从“只看第一张图”改为逐张物理页面输出。</p></div>
    <div class="timeline-item"><strong>7 月 26 日下午以后：焊口编号试验</strong><p>当主要图形、文字和版面已能大部分还原后，才开始把 PCF 的焊口号匹配到 SHA 黑点，并研究菱形、引线与避让。</p></div>
  </div>
  <div class="callout"><strong>不是按次数硬凑：</strong>每一轮都是由一个看得见的问题触发，例如“材料表整体偏左”“中文没有显示”“方框文字太扁”“法兰细线缺失”“焊口引线没有连到黑点”。</div>
`) }

${page("从第一张原型到可读图纸", `
  <div class="image-row">
    <div><div class="image-frame"><img src="${images.firstPrototype}" /></div><div class="caption"><strong>第一版，7 月 25 日 23:03：</strong>页面、文字和框线都刚开始出现；材料表重叠，图形缺失明显。</div></div>
    <div><div class="image-frame"><img src="${images.lineStage}" /></div><div class="caption"><strong>中间阶段：</strong>主线、双线、尺寸和部分注释已出现，但构件节点和图框仍不完整。</div></div>
    <div><div class="image-frame"><img src="${images.directStage}" /></div><div class="caption"><strong>全页 SHA 还原：</strong>图框、左侧单线图、右侧材料表、右下角标题栏已成为一个完整页面。</div></div>
  </div>
  <div class="callout">这三张图都来自同一份 SHA 的真实阶段输出。它们说明工作路线是：先让信息“出现”，再让位置、线型、字体和构件“接近原图”，最后才加焊口等新信息。</div>
`) }

${page("逐元素案例一：主管线、虚线、流向和尺寸", `
  <div class="two-col">
    <div><div class="image-frame"><img src="${images.lineStage}" /></div><div class="caption"><strong>中间阶段：</strong>主线、双线和部分尺寸已出现，但局部节点有放射状的辅助线，文字框、仪表和尺寸列的相对关系仍不稳定。</div></div>
    <div><div class="image-frame"><img src="${images.directStage}" /></div><div class="caption"><strong>当前阶段：</strong>主管双线、保温虚线、流向箭头、尺寸延长线、尺寸数字、仪表圆圈和连接说明共同落在同一纸面坐标系统内。</div></div>
  </div>
  <table class="mini-table"><tr><th>元素</th><th>早期问题</th><th>从 SHA 找到的处理依据</th><th>修正结果</th></tr>
  <tr><td>主管线 / 虚线</td><td>只读到部分线段，线宽和虚线关系不稳定。</td><td>物理 Sheet 的两点线与组合线；StyleCluster 中已验证的线宽引用。</td><td>恢复双线、虚线和粗细层次。</td></tr>
  <tr><td>流向箭头</td><td>有时只剩管线，箭头不完整。</td><td>Sheet 中与管段相邻的箭头/组合笔画和二维方向。</td><td>箭头跟随原管段方向，而不是由 PCF 坐标猜位置。</td></tr>
  <tr><td>178 / 154 等尺寸</td><td>使用 PSM 外框左边界时，数字偏离竖向尺寸列。</td><td>Sheet 文字锚点给列位置；PSM 只提供字形高宽；尺寸线来自 Sheet。</td><td>数字回到尺寸列旁，字体比例更接近原图。</td></tr></table>
`, "case-page") }

${page("逐元素案例二：材料表、图框和右下角信息", `
  <div class="two-col">
    <div><div class="image-frame"><img src="${images.firstPrototype}" /></div><div class="caption"><strong>第一版：</strong>右侧材料表文字相互重叠，图框和右下角标题区只有零散内容，无法作为正式图纸使用。</div></div>
    <div><div class="image-frame"><img src="${images.titleDetail}" /></div><div class="caption"><strong>当前局部：</strong>材料表列、版本行、图框网格、项目名、中文公司名、图号和页码已能按 SHA 的锚点、范围和模板规则排版。</div></div>
  </div>
  <table class="mini-table"><tr><th>区域</th><th>如何还原</th><th>偏移如何解决</th></tr>
  <tr><td>材料表</td><td>表格格线来自图框/页面线；条目来自当前物理页文字；字体范围来自 PSM，右侧模板区采用单独字体规则。</td><td>不再把 PSM 外框左边界当文字起点；按 Sheet 锚点与 PSM 高宽组合定位，解决整体偏左和行高不对。</td></tr>
  <tr><td>外框、内部框、版本行</td><td>共享模板 Sheet221 的框线与固定标签，加上当前页修订数据。</td><td>先统一页面视口，再恢复模板坐标；避免对每一段框线单独平移。</td></tr>
  <tr><td>右下图号、页码、中文</td><td>固定文本来自模板，项目/修订字段来自 TaggedTxtData 或绑定字段；中文使用 UTF-16 文本和相邻 PSM/样式证据。</td><td>不同文本族分别处理：有些用 Sheet x 锚点，有些模板文字使用 PSM 包围范围，不能用同一个公式硬套。</td></tr></table>
`, "case-page") }

${page("逐元素案例三：法兰、方框、引线与仍未闭合的细节", `
  <div class="two-col">
    <div><div class="image-frame"><img src="${images.leftCallouts}" /></div><div class="caption"><strong>当前左侧局部：</strong>法兰/变径附近的双线、方框标记、引线、焊缝黑点、尺寸和注释已由同一页 SHA 图元还原。</div></div>
    <div><div class="image-frame"><img src="${images.complex}" /></div><div class="caption"><strong>复杂节点对照：</strong>PDF 只用来指出“哪里还有多线或少线”；修正仍必须回 SHA 找到对应的 18/32 线段、组合记录和父子关系。</div></div>
  </div>
  <table class="mini-table"><tr><th>元素</th><th>目前已解决</th><th>仍需继续</th></tr>
  <tr><td>法兰 / 变径 / 阀门主体</td><td>已恢复普通线段、18/32 线段和部分组合图元的主体轮廓；方向由二维笔画决定。</td><td>密集法兰/阀门交界处仍有辅助细线和复合子图元未完全解释，不能为“像 PDF”而盲删。</td></tr>
  <tr><td>方框标记</td><td>已验证一类“文字引用后连续四边”的组合关系，可由 SHA 闭合出框；框内字用 PSM 比例控制。</td><td>不是所有四边形都可自动当作文字框，仍需按已验证的构件类别限制规则。</td></tr>
  <tr><td>引线</td><td>引线由 Sheet 的线段/组合笔画恢复，文字按同一图元组的锚点与方向定位。</td><td>新增焊口引线的自动避让仍在试验，需要逐条验证是否指向正确黑点、是否与构件交叉。</td></tr></table>
  <div class="callout"><strong>这里的原则：</strong>“识别到的主体法兰”可以进入矢量库；“密集节点里尚不确定的辅助笔画”必须保留来源和低置信度，不能混进标准构件模板。</div>
`, "case-page") }

${page("SHA 文件里大致装了什么", `
  <p class="lead">SHA 不是一张图片，也不是简单的文字表。它更像一个装着很多“抽屉”的图纸包：有的抽屉装页面线条，有的装文字，有的装样式，有的装对象编号和位置范围。<span class="highlight">本页只归纳最关键的 6 类，不代表 SHA 只有这些部分。</span></p>
  <div class="architecture">
    <div class="result"><strong>Sheet6 / 其他 Sheet</strong><p>每张物理图纸的二维线条、文字锚点、尺寸、引线、框线和许多构件笔画。不能假设只有 Sheet6，一份 SHA 可能有多张图。</p></div>
    <div class="result"><strong>Sheet221</strong><p>共享图框和标题栏模板：外框、分格线、固定标题文字等。右下角的许多固定内容来自这里。</p></div>
    <div class="result"><strong>PSMcluster0</strong><p>每个图形或文字在纸面上的范围信息。它适合提供文字高度、宽度和图形包围范围，但不总是文字真正的起笔点。</p></div>
    <div class="result"><strong>StyleCluster</strong><p>已验证可用于读取一部分线宽、字体族和字体比例。它决定“这根线粗不粗、这段字像不像”，不是只靠我们自己设定。</p></div>
    <div class="result"><strong>动态属性 / UCI</strong><p>把模型对象与图纸对象串起来的内部编号。焊缝黑点、构件与其可见图元的关联主要依靠这条链路。</p></div>
    <div class="result"><strong>TaggedTxtData 与 JSite</strong><p>前者包含修订、标题等绑定字段；后者可能是外部资源或图标。没有完整内容的外部资源会保留为“待解”，不会凭 PDF 猜出来。</p></div>
  </div>
  <div class="callout"><strong>目前的边界：</strong>线、文字、方框、椭圆/黑点、部分组合图元已经可以读取；复杂法兰、阀门周边的一些辅助笔画和完整父子关系仍在继续解码。</div>
`) }

${page("一个真实 SHA 的架构实例", `
  <p class="lead">下面不是抽象示意，而是从 <span class="highlight">N400P3A-AMSS2-N444201-01-0.sha</span> 读取到的实际目录。该文件共有 <span class="highlight">50 个流</span>；为了便于阅读，树中把它们归并为 7 组，<strong>不是说它只有 7 个部分</strong>。它有 5 张物理 ISO 页，因此除了共享内容外，也有多个页面 Sheet。</p>
  <div class="tree">N400P3A-AMSS2-N444201-01-0.sha  （一个 OLE / Shape2D 图纸包）
├─ Sheet6、Sheet34246、Sheet36113、Sheet5563、Sheet7763
│  └─ 5 张物理 ISO 页：主体管线、文字、尺寸、引线、构件笔画
├─ Sheet221
│  └─ 共用图框、标题栏网格、固定标签
├─ PSMcluster0、PSMspacemap、PSMroots
│  └─ 图元/文字的纸面范围、空间索引、部分层级关系
├─ StyleCluster
│  └─ 已验证的一部分线宽、字体及比例信息
├─ Unclustered Dynamic Attributes
│  └─ UCI 与内部图元引用：把模型对象和纸面对象串起来
├─ TaggedTxtData/Revision、TitleArea、Notes、Configuration ...
│  └─ 修订、标题、注释、配置等可变数据
└─ JSite690、JSite1402
   └─ 外部资源/图标内容；有内容时可以读取，没有时保留待解</div>
  <table class="mini-table"><tr><th>这份样本的实际数量</th><th>它说明什么</th></tr>
  <tr><td>5 个非空物理页面 Sheet；另有空/辅助 Sheet</td><td>不能只读 Sheet6；一张 SHA 可以装同一条管线的多张 ISO 页，空 Sheet 也不应被误当作物理图纸。</td></tr>
  <tr><td>约 75 KB 的 PSMcluster0</td><td>它是二维布局的重要索引，但不是所有图元语义都已完全解码。</td></tr>
  <tr><td>约 170 KB 的动态属性</td><td>为 UCI、构件和纸面图元之间建立可追溯关系，是焊口匹配的重要依据。</td></tr></table>
  <p class="small">本样本中未在树上逐项展开的内容还包括：文档摘要信息、版本记录、应用对象、动态属性元数据、JSite 清单、标记文本清单、PSM 表与分段表等。这些目前有的仅完成盘点，有的仍在解码，不能简单删除或假设无用。</p>
  <div class="callout"><strong>读取顺序：</strong>先找物理页，再读页面线条和文字锚点；随后用 PSM 校正范围，用 StyleCluster 校正样式，用 UCI 连接工程对象。任何一层缺失，都不能用 PDF 数据替代。</div>
`) }

${page("SHA 是怎样得到的？它和 PCF 是否绑定", `
  <p class="lead">对本项目样本，可以确认的是：SHA 元数据写明创建应用为 <span class="highlight">Shape2DServer Application</span>，模板为 <span class="highlight">PANDA3_IFC.Sha</span>。这说明它是 Shape2DServer 写出的二维图纸容器；但单凭文件本身，不能证明它与旁边的 PCF 一定来自同一次导出。</p>
  <div class="flow">
    <div class="step"><div class="number">A</div><h2>三维模型 / 设计数据</h2><p>管道、构件、连接、材料、焊接和属性。不同项目可能来自 PDMS、Smart 3D 或其他设计系统。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">B</div><h2>PCF 或 IDF</h2><p>以工程数据交换文件形式描述构件、连接和坐标；可作为 ISO 引擎输入。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">C</div><h2>ISO 引擎 + 项目样式</h2><p>决定纸张大小、分图、字体、材料表、构件符号、焊口表现和布局。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">D</div><h2>PDF、SHA、PCF 等输出</h2><p>PDF 是最终可阅览图；本项目中的 SHA 是 Shape2D 形式的二维出图容器，可继续被解析和试验性写入。</p></div>
  </div>
  <table class="mini-table"><tr><th>问题</th><th>结论</th></tr>
  <tr><td>哪个软件能导出 SHA 与 PCF？</td><td>Hexagon Smart 3D 的官方 Save As 文档明确列出 Isogen 等轴图可选 Shape2DServer SHA、PCF，或同时导出两者。因此<strong>同一个 Smart 3D 导出动作可以产生 SHA + PCF</strong>。</td></tr>
  <tr><td>当前这批 SHA 与 PCF 是否已证实同次导出？</td><td><strong>尚未证实。</strong>文件名、管线内容和可匹配对象支持它们是同一张图的候选配对；若要证明“同次导出”，需要 Smart 3D/Isogen 的出图日志、导出配置或一次受控复现实验。</td></tr>
  <tr><td>PCF 有了，必然有同名 SHA 吗？</td><td><strong>不必然。</strong>PCF 可以单独存在；是否生成 SHA 取决于项目使用的出图引擎、版本和输出选项。</td></tr>
  <tr><td>SHA 有了，必然能找到 PCF 吗？</td><td><strong>不必然。</strong>SHA 可以独立保存为历史图纸包。它可能保留 UCI/内部引用，但原 PCF 文件未必仍在目录中。</td></tr>
  <tr><td>二者如何关联？</td><td>优先用图纸号/管线号、构件类型、连接关系、尺寸及可用的 UCI 做证据链；相同文件名前缀只是弱证据，不能单独当作绑定证明。</td></tr></table>
  <div class="callout">官方文档支持两件事：Smart 3D 可以导出 SHA、PCF 或两者；Isogen/Smart Isometrics 可读取 PCF/IDF。<strong>文档没有证明 IDF 一定会同时生成 SHA</strong>，因此该关系必须用贵项目的原始出图环境做黑箱验证。</div>
`, "source-page") }

${page("如果输入是 IDF，有没有类似的文件和信息", `
  <p class="lead">IDF 不是某一个软件专属的“另一种 SHA”。它是 Isogen 可接受的中间工程数据格式：不同设计系统都可能生成 IDF 或 PCF；同一个系统也可能根据输出选项生成其中之一。SHA 则是本项目中由 Shape2DServer 保存下来的二维纸面图纸包。</p>
  <table class="mini-table"><tr><th>层次</th><th>PCF / IDF 通常能提供</th><th>SHA 通常能提供</th></tr>
  <tr><td>工程内容</td><td>管子、法兰、弯头、阀门、支管、坐标、属性、部分焊口/切管信息。</td><td>可见构件与部分内部对象引用，但重点不是完整工程模型。</td></tr>
  <tr><td>图纸版面</td><td>通常不提供最终的二维文字位置、引线走向、方框大小、材料表排版或图框格线。</td><td>提供已出图的二维线条、文字锚点、框线、尺寸、表格和图框位置。</td></tr>
  <tr><td>哪个软件导出 IDF？</td><td>没有唯一答案。官方资料说明多个设计系统可把 IDF 或 PCF 作为 Isogen 输入；Smart Isometrics、Spoolgen 等也能导入两者。是否由 PDMS、PDS、AutoPlant、Smart 3D 或其他系统导出，要以项目出图日志/配置为准。</td><td>本样本文件元数据只证明 SHA 由 Shape2DServer 写入，不能反推出最初 IDF 的来源软件。</td></tr>
  <tr><td>和 ISO 的关系</td><td>可作为 ISO 引擎输入。官方资料明确说明 PCF 可转换为 IDF，且 IDF/PCF 都可被 Isogen 类流程读取。</td><td>当前没有找到官方证据证明“输入 IDF 就必然输出 SHA”；它不是 IDF 的通用标准伴随文件。</td></tr>
  <tr><td>如果只有 IDF</td><td>可以像 PCF 一样作为未来自动出图的工程输入；但要复刻历史 PDF 的布局，仍需项目样式、分图规则、构件模板和排版引擎。</td><td>如果项目引擎能输出 SHA，SHA 可成为验收与精细复用的二维依据；否则应输出 SVG/PDF 等自有格式。</td></tr></table>
  <div class="callout"><strong>建议做一次小测试，而不是猜：</strong>选一条同时有 PCF、IDF、PDF、SHA 的管线，用原项目配置分别输入 PCF 和 IDF，记录输出了哪些文件、图号是否一致、UCI 是否保留、焊口和分页是否一致。这个测试可以最终确认贵项目中的“IDF → SHA”真实链路；在此之前，报告中应写“尚未找到/尚未验证”，而不是默认存在。</div>
`) }

${page("图纸左侧：单线图每一部分由什么决定", `
  <table class="mini-table"><tr><th>图上看到的内容</th><th>SHA 中的主要来源</th><th>PCF 起到的作用</th></tr>
  <tr><td>主管、支管、双线、虚线</td><td>当前物理 Sheet 的两点线段和组合线段；线型/线宽由 Sheet 的样式引用连接到 StyleCluster。</td><td>提供管道连接关系与三维工程坐标，可校验这段管子属于谁；不提供纸面画线位置。</td></tr>
  <tr><td>流向箭头</td><td>Sheet 中与管线相邻的箭头/组合笔画，以及其自身二维坐标和样式。</td><td>可提供介质/方向相关工程语义，但最终箭头的位置和朝向由 SHA 纸面图元决定。</td></tr>
  <tr><td>长度尺寸、尺寸箭头、178/154 等数字</td><td>尺寸线、延长线来自 Sheet；数字有文字锚点，PSM 提供字形范围，经过样式规则校正。</td><td>可提供真实工程长度用于核查；不应拿 PCF 坐标直接替代纸面位置。</td></tr>
  <tr><td>法兰、弯头、变径、阀门</td><td>普通线段加 18/32 线段族和组合图元。朝向由这些二维笔画的相对位置决定。</td><td>告诉我们构件类型、规格、连接关系，是未来“PCF 单独出图模板”的基础。</td></tr>
  <tr><td>焊缝黑点 / 连接点</td><td>小椭圆或点图元的真实锚点，加上动态属性 UCI 与 PSM 范围。</td><td>提供焊口编号、焊接类型等工程数据；用于把编号匹配到正确黑点。</td></tr>
  </table>
  <div class="callout"><strong>简单说：</strong>左侧图形“画在哪里、画成实线还是虚线、箭头朝向哪里”主要由 SHA 决定；PCF 负责告诉我们这些图形在工程上分别是什么。</div>
`) }

${page("图纸右侧和右下角：为什么也要分开处理", `
  <table class="mini-table"><tr><th>区域</th><th>主要由什么决定</th><th>曾经遇到的偏移 / 处理方法</th></tr>
  <tr><td>右侧材料表</td><td>表格边线来自图框/页面线条；材料条目来自当前页文字；字体高度和宽度主要参考 PSM，字体族/线宽参考 StyleCluster。</td><td>早期文字整体偏左、行高不对。不能拿 PDF 坐标硬挪，而是区分“文字锚点”和“PSM 字形外框”，对右侧模板文字采用单独规则。</td></tr>
  <tr><td>右下角标题栏、页码、图号</td><td>固定网格和固定标签大多来自 Sheet221；项目名、修订、日期等来自 TaggedTxtData 或页面绑定字段。</td><td>早期图号/页码与下方标题偏左。通过保留 Sheet 锚点，并从同一 SHA 的 PSM 取高度/宽度来调整，而不是对照 PDF 抄坐标。</td></tr>
  <tr><td>中文公司名、项目名</td><td>SHA 中的 UTF-16 文字、字体样式引用和相邻 PSM 范围。</td><td>早期无法显示或尺寸不对。后来单独识别中文记录与字体绑定，使用 SHA 的锚点和实际字形范围。</td></tr>
  <tr><td>版本行、审阅人、日期</td><td>固定表格来自模板；可变内容来自修订数据和绑定字段。</td><td>如果没有 PSM 范围，采用已验证的样式比例；仍保留为“样式推导”，不伪装成完全解码。</td></tr>
  </table>
  <div class="callout"><strong>为什么右侧看起来更难：</strong>它混合了固定模板、每页变化的数据和不同字体。左侧单线图可以按构件理解；右侧必须同时理解“表格格子、文本锚点、字体比例和数据来源”。</div>
`) }

${page("文字、方框、引线、偏移：具体怎么找原因", `
  <div class="three">
    <div class="card"><h2>普通注释</h2><p>SHA 的文字记录给出内容和插入锚点；PSM 给出文字实际占用的高宽。早期直接用 PSM 左边界，很多注释显得偏下或偏左。</p><p class="role">修正：已验证的样式使用文字锚点定位，再用 PSM 控制大小。</p></div>
    <div class="card"><h2>带方框标记</h2><p>文字旁的方框不是后来猜画的。部分记录存在“文字引用后连续四条边”的组合关系，可直接闭合成矩形。</p><p class="role">修正：框决定位置，PSM 的高宽比例决定框内字的形状。</p></div>
    <div class="card"><h2>引线和尺寸</h2><p>引线、尺寸线和箭头来自 Sheet 的线段/组合笔画。文字不应随意贴到线端，而要使用同一图元组的锚点、方向和样式。</p><p class="role">修正：按原始线段方向和文字基线放置，避免只用视觉估计。</p></div>
  </div>
  <div class="callout"><strong>偏移问题的真实原因通常不是一个“x/y 写错”：</strong>常见原因包括页面视口取错、字体度量不同、PSM 记录的是外框而不是基线、文字/图框属于不同组合关系，以及模板区和左侧单线图区采用不同字体规则。</div>
`) }

${page("示例一：先把整张图从 SHA 画出来", `
  <div class="image-frame"><img src="${images.comparison}" /></div>
  <div class="caption"><strong>左边：</strong>原始 PDF 样张。<strong>右边：</strong>只读取 SHA 还原得到的图。这个对比用于找问题，例如文字锚点、构件细节、表格位置；它不是把左图的内容复制到右图。</div>
  <div class="callout">经过多轮修正，图框、主要管线、流向箭头、尺寸、常见构件、材料表和大部分文字都已能从 SHA 中重新生成。复杂区域仍有细节差异，后面会说明原因。</div>
`) }

${page("示例二：焊口编号怎样放回图纸", `
  <div class="two-col">
    <div><div class="image-frame"><img src="${images.reconstructed}" /></div><div class="caption">先由 SHA 还原：管线本身、焊缝黑点和已有标记都在正确的纸面空间里。</div></div>
    <div><div class="image-frame"><img src="${images.welded}" /></div><div class="caption">再把 PCF 的焊口编号对应到 SHA 黑点，生成菱形编号、引线和避让后的文字位置。</div></div>
  </div>
  <div class="callout"><strong>重要：</strong>菱形、引线、焊口号不是只在 Python 图片里临时画上去。试验流程会生成一个新的 SHA 副本，再由 SHA 解析程序读取并输出图纸。因此这个结果可继续交给同类引擎处理。</div>
`) }

${page("焊口标记的处理逻辑", `
  <div class="two-col">
    <div>
      <h2>一个焊口，分成三步理解</h2>
      <table class="mini-table"><tr><th>步骤</th><th>实际做的事</th></tr>
      <tr><td>1. 在 PCF 找编号</td><td>例如 S001、S002。它说明焊口属于哪段连接关系。</td></tr>
      <tr><td>2. 在 SHA 找黑点</td><td>利用构件和连接关系，把 PCF 的焊口对应到纸面上已有的焊缝黑点。</td></tr>
      <tr><td>3. 在 SHA 新增标记</td><td>写入菱形、编号和引线。标记尽量放在管段外侧，并按相邻管段方向安排。</td></tr></table>
      <h2 style="margin-top:6mm">排版不是随机的</h2>
      <p>同一根直管上的焊口尽量放在同一侧；引线尽量垂直于主管段；菱形避开文字和构件；密集区域允许少量折中，但不能失去“编号指向哪个黑点”的关系。</p>
      <div class="callout">这部分仍会继续优化，目标不是“所有引线绝不相交”，而是让绝大多数图纸看起来有一致的工程排版规律。</div>
    </div>
    <div><div class="image-frame"><img src="${images.fullPage}" /></div><div class="caption">整页对比也用于检查：焊口标记必须跟着正确的焊缝黑点，而不能只落在附近。</div></div>
  </div>
`) }

${page("已经逐步补齐了哪些内容", `
  <div class="result-grid">
    <div class="result"><strong>图纸分页</strong><p>识别同一 SHA 中的多张物理图纸，按页输出 SVG 和 PNG。</p></div>
    <div class="result"><strong>管线与常见构件</strong><p>恢复双线、虚线、流向箭头、焊缝黑点、法兰、弯头、阀门及部分仪表符号。</p></div>
    <div class="result"><strong>文字与注释</strong><p>恢复普通文字、带框文字、引线、尺寸文字、连接说明、中文标题等，并多轮校正锚点。</p></div>
    <div class="result"><strong>图框与表格</strong><p>恢复页面外框、内部框线、标题栏、版本栏、材料表与右下角信息区。</p></div>
    <div class="result"><strong>焊口关联</strong><p>把 PCF 中的焊口记录匹配到 SHA 的可见位置，再尝试生成带编号的图纸。</p></div>
    <div class="result"><strong>可复用规则</strong><p>同规格、同朝向的构件可以采用同一套“画法模板”；但不能假设不同 SHA 的底层记录完全一模一样。</p></div>
  </div>
  <p style="margin-top:5mm">当前样本范围内，已完成 <span class="highlight">7 份图纸、28 张物理页面</span> 的基础 SHA 还原输出。PCF 中共识别到 370 条焊口记录，其中 335 条在 SHA 中找到了可见的对应点，可用于后续编号出图实验。</p>
`) }

${page("三个文件夹的覆盖范围与当前质量状态", `
  <p class="lead">以下统计截至 2026-07-27。先区分两件事：<span class="highlight">“已盘点”</span>只表示已读到 SHA 结构与物理页数；<span class="highlight">“已深度复核”</span>才表示已输出图、逐页与 PDF 对照并记录问题。</p>
  <table class="mini-table"><tr><th>文件夹</th><th>SHA 数量</th><th>识别出的物理 ISO 页</th><th>当前处理状态</th></tr>
  <tr><td>0011</td><td>2</td><td>6</td><td>已完成结构盘点；尚未逐页生成和视觉验收。</td></tr>
  <tr><td>0012</td><td>49</td><td>60</td><td>已完成结构盘点；尚未逐页生成和视觉验收。</td></tr>
  <tr><td>0013</td><td>335</td><td>519</td><td>其中 6 个 SHA、24 页已深度复核；其余 329 个 SHA、495 页尚未逐页验收。</td></tr>
  <tr><td><strong>合计</strong></td><td><strong>386</strong></td><td><strong>585</strong></td><td><strong>已盘点 386/386；已深度复核 6/386 个 SHA、24/585 页。</strong></td></tr></table>
  <h2 style="margin-top:5mm">已深度复核的 24 页，按当前基础还原质量分类</h2>
  <table class="mini-table"><tr><th>分类</th><th>图纸 / 页数</th><th>含义</th></tr>
  <tr><td>基本没问题</td><td>4 个 SHA，13 页：CHW-N491163（1）、CHW-N434591（2）、LS-N492164（3）、RHO1-N434201（7）</td><td>主线、图框、文字、材料表、尺寸、常见构件和标题栏已逐页复核，没有发现需要立刻修复的明显缺失。</td></tr>
  <tr><td>部分可能有问题</td><td>2 个 SHA，11 页：AMSS2-N444201（5）、UA-N495128（6）</td><td>整页可用且大部分内容正确；复杂法兰/阀门密集节点附近仍有辅助细线或组合构件细节待进一步解码。</td></tr>
  <tr><td>明显有问题</td><td>0 个 SHA，0 页</td><td>在已深度复核范围内，没有发现整页失真、主体管线缺失或图框/材料表无法使用的页面。</td></tr></table>
  <div class="callout"><strong>补充说明：</strong>此前 7 个 SHA、28 页的样本还包括文件夹外的“100P3A-LN-276994-01”（4 页）。它已复核，但不计入上述“三个文件夹”的 386 个 SHA 统计。剩余 <strong>380 个 SHA、561 页</strong> 尚未逐页视觉验收，应标记为“未评估”，而不是“没问题”。</div>
`) }

${page("下一步工作路线：从样本到自动出图", `
  <p class="lead">下面是基于当前成果形成的路线。四条工作并非完全串行，但必须先把真实 SHA 的规律积累到足够稳定，再让 PCF 单独承担出图任务。</p>
  <div class="roadmap">
    <div class="step"><strong>1. 近期：覆盖全部 SHA</strong><p>逐步还原待处理的 380 个 SHA、561 页。</p><p>按页验收主体管线、图框、材料表、文字、尺寸、法兰/阀门、仪表和引线。</p><p>输出“基本没问题 / 局部待解 / 明显异常”台账，不把未评估页面算进通过率。</p></div>
    <div class="step"><strong>2. 并行：PCF 焊口写入 SHA</strong><p>验证 PCF 焊口能否对应 SHA 的黑点/UCI。</p><p>对直管缺少可见焊点的情况，试验新增黑点、菱形编号和引线。</p><p>自动检查：编号不丢、引线连接正确、同管段同侧、尽量垂直、避免重叠。</p></div>
    <div class="step"><strong>3. 中期：沉淀为规则和矢量库</strong><p>形成法兰、弯头、变径、阀门、仪表、文字、方框、尺寸、图框和材料表的模板与布局规则。</p><p>同时将 SHA 已解码的真实矢量笔画按构件类型、规格、朝向、连接口和文字区域入库。</p><p>未来 PDF 单线图识别程序可用该库判定“这里更像法兰还是阀门”，不再只依赖图像外观。</p></div>
    <div class="step"><strong>4. 长期：学习分图规律</strong><p>从历史多页 SHA 和 PCF 中学习同一管线在哪里拆页。</p><p>建立可解释的容量评分，再引入回归模型：综合长度、折弯、分支、构件密度、文字密度、图框可用空间等特征。</p><p>输出候选分图点与每页拥挤度。</p></div>
  </div>
  <div class="callout"><strong>长期边界：</strong>“PCF/IDF 生成新的 SVG/PDF”是可控目标；“PCF/IDF 生成能被原厂引擎完全接受的原始 SHA”需要继续验证写入规范、项目资源和引擎兼容性，不能在现阶段承诺完全等价。</div>
`) }

${page("长期补强：用 SHA 建立单线图矢量构件库", `
  <p class="lead">当前的 PDF 单线图识别程序如果只看 PDF 矢量，常常只能判断“这里有一组线”。SHA 的价值在于：它能给许多线组补上来源、UCI、构件关系和真实二维笔画，从而变成带标签的训练样本。</p>
  <div class="flow">
    <div class="step"><div class="number">1</div><h2>SHA 解码</h2><p>取出法兰、阀门、弯头、变径、仪表等真实笔画，以及其二维位置、朝向和连接点。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">2</div><h2>PCF / UCI 标注</h2><p>用构件类型、规格、连接关系和可用 UCI 给这些笔画附上可信标签。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">3</div><h2>形成矢量库</h2><p>每个条目保存：笔画拓扑、方向、比例、连接口、常见文字/引线区域和置信度。</p></div>
    <div class="arrow">→</div>
    <div class="step"><div class="number">4</div><h2>反哺 PDF 识别</h2><p>未来仅有矢量 PDF 时，按线条组合、朝向和连接关系匹配库，判断它是法兰、阀门或其他构件。</p></div>
  </div>
  <table class="mini-table"><tr><th>库中应保存的字段</th><th>用途</th></tr>
  <tr><td>构件类别、规格、朝向、连接口</td><td>减少“同一构件旋转后就认不出”的问题。</td></tr>
  <tr><td>真实 SVG/Shape2D 笔画组合</td><td>让 PDF 识别比对矢量结构，而不是只做截图识别。</td></tr>
  <tr><td>文字框、引线、尺寸和附近黑点的相对区域</td><td>把构件周边的工程标注一起作为识别证据。</td></tr>
  <tr><td>来源 SHA/页/UCI/PCF 证据与置信度</td><td>可追溯；不把尚未确认的复杂笔画混进“标准法兰模板”。</td></tr></table>
  <div class="callout"><strong>边界：</strong>这条路线适用于“PDF 仍保留矢量线条”的情况。若 PDF 已被扫描成图片，还要增加图像识别、OCR 和线段矢量化步骤；SHA 矢量库仍可作为符号模板和校验依据。</div>
`) }

${page("哪些地方还需要继续攻关", `
  <div class="two-col">
    <div><div class="image-frame"><img src="${images.complex}" /></div><div class="caption">复杂法兰、阀门和仪表区域：左为 PDF，右为 SHA 还原图。这里最容易出现局部线条、黑点、文字相对位置的差异。</div></div>
    <div>
      <h2>目前最难的不是“画线”</h2>
      <p>SHA 里很多细线、局部轮廓和辅助图元都真实存在，但它们不总是直接告诉我们“这是一只法兰的哪条边”。同一个构件在不同页面附近还可能被文字、引线、遮挡关系影响。</p>
      <h2>已经形成的处理原则</h2>
      <ul class="simple-list"><li>不根据 PDF 的图形反推或篡改 SHA；PDF 只负责提示问题。</li><li>每发现一类偏移，就回到 SHA 的原始图元、样式和坐标关系找原因。</li><li>优先形成“构件模板 + 朝向 + 周边布局”的可复用规则。</li><li>对尚未完全理解的底层记录保留证据，不强行猜测其语义。</li></ul>
      <div class="callout"><strong>下一步：</strong>继续解码复杂构件的辅助图元和可见性规则，同时丰富法兰、变径、仪表等模板。这样未来只有 PCF 时，也能按统一的视觉规则生成新的单线图；而要追求与历史图纸最接近的版面时，SHA 仍是最好的来源。</div>
    </div>
  </div>
`) }

${page("给同事的一页结论", `
  <p class="lead">我们现在已经不是“把 PDF 截成图片”，而是在建立一条可追溯的图纸还原路线：<span class="highlight">PCF 提供工程关系，SHA 提供纸面图形，PDF 提供视觉验收。</span></p>
  <table class="mini-table"><tr><th>想做什么</th><th>现在能做到什么</th></tr>
  <tr><td>看懂历史 ISO 图纸</td><td>可以从 SHA 抽出页面、图框、主要管线、文字和材料表，并输出清晰 SVG / PNG。</td></tr>
  <tr><td>给焊口加编号</td><td>可以用 PCF 的焊口记录对应 SHA 的纸面位置，生成带菱形编号和引线的 SHA 副本进行出图实验。</td></tr>
  <tr><td>判断同一条线拆成几张图</td><td>可以从 SHA 的页面结构和 PCF 的连接关系中找线内分图位置，而不把“连接到另一条线”误判为分图点。</td></tr>
  <tr><td>未来只有 PCF 时自动出图</td><td>可以逐步建设构件画法模板。相同规格、相同方向的法兰等构件可复用同一视觉规则，但仍需要独立的排版引擎。</td></tr></table>
  <div class="callout" style="margin-top:7mm"><strong>最重要的成果：</strong>这是一套能继续生长的过程。每多分析一种 SHA 图元，就多沉淀一条规则；下一份图纸不需要从零开始，而是在已有规则上继续补齐。</div>
  <p class="small" style="margin-top:6mm">附注：本 PDF 所用示例均来自本次工作区的真实 SHA、PCF、PDF 对比和生成结果。为避免误导，本文不把 PDF 当作反向数据源，也不把尚未验证的底层记录包装成确定结论。</p>
`) }
</body>
</html>`;

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1684, height: 1191 }, deviceScaleFactor: 1 });
  await page.setContent(html, { waitUntil: "load" });
  await page.emulateMedia({ media: "print" });
  await page.pdf({ path: outputPath, format: "A4", landscape: true, printBackground: true, preferCSSPageSize: true });
} finally {
  await browser.close();
}
console.log(outputPath);
