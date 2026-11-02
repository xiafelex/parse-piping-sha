#!/usr/bin/env node
/** Render one complete SHA-derived SVG page at its declared physical size. */

import { pathToFileURL } from "node:url";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("Usage: node render_svg_pdf.mjs input.svg output.pdf");
  process.exit(1);
}

const jobs=input==='--manifest' ? JSON.parse(await readFile(output,'utf8')) : [{input,output}];
if (!Array.isArray(jobs) || jobs.length===0) throw new Error('Empty page manifest');
const launchOptions = process.platform === "win32"
  ? { headless: true, channel: "msedge" }
  : { headless: true };
const browser = await chromium.launch(launchOptions);
try {
for (const {input,output} of jobs) {
const svgSource = await readFile(input, "utf8");
const widthMatch = svgSource.match(/data-page-width-mm="([0-9]+(?:\.[0-9]+)?)"/);
const heightMatch = svgSource.match(/data-page-height-mm="([0-9]+(?:\.[0-9]+)?)"/);
if (!widthMatch || !heightMatch) {
  console.error("SVG does not declare data-page-width-mm/data-page-height-mm");
  process.exit(1);
}
const widthMm = Number(widthMatch[1]);
const heightMm = Number(heightMatch[1]);
if (!(widthMm > 0 && heightMm > 0 && widthMm <= 2000 && heightMm <= 2000)) {
  console.error(`Invalid SVG physical page size: ${widthMm} x ${heightMm} mm`);
  process.exit(1);
}
const pageWidth = `${widthMm}mm`;
const pageHeight = `${heightMm}mm`;

  const page = await browser.newPage();
  const sourceUrl = `data:image/svg+xml;base64,${Buffer.from(svgSource).toString("base64")}`;
  await page.setContent(
    `<html><body><img src="${sourceUrl}" /></body></html>`,
    { waitUntil: "load" },
  );
  await page.addStyleTag({
    content: [
      `@page{size:${pageWidth} ${pageHeight};margin:0}`,
      "html,body{margin:0;width:100%;height:100%;overflow:hidden;background:white}",
      "img{display:block;width:100%;height:100%;object-fit:contain}",
    ].join(""),
  });
  await page.locator("img").evaluate((image) => image.decode());
  await page.pdf({
    path: output,
    width: pageWidth,
    height: pageHeight,
    margin: { top: "0", right: "0", bottom: "0", left: "0" },
    printBackground: true,
    preferCSSPageSize: true,
  });
  await page.close();
  console.log(`${pathToFileURL(input).href} -> ${output}`);
}
} finally {
  await browser.close();
}
