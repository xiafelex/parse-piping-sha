#!/usr/bin/env node
/** Render one complete SHA-derived SVG page to a single-page A1 landscape PDF. */

import { pathToFileURL } from "node:url";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("Usage: node render_svg_pdf.mjs input.svg output.pdf");
  process.exit(1);
}

const launchOptions = process.platform === "win32"
  ? { headless: true, channel: "msedge" }
  : { headless: true };
const browser = await chromium.launch(launchOptions);
try {
  const page = await browser.newPage();
  const sourceUrl = `data:image/svg+xml;base64,${Buffer.from(await readFile(input)).toString("base64")}`;
  await page.setContent(
    `<html><body><img src="${sourceUrl}" /></body></html>`,
    { waitUntil: "load" },
  );
  await page.addStyleTag({
    content: [
      "@page{size:841mm 594mm;margin:0}",
      "html,body{margin:0;width:100%;height:100%;overflow:hidden;background:white}",
      "img{display:block;width:100%;height:100%;object-fit:contain}",
    ].join(""),
  });
  await page.locator("img").evaluate((image) => image.decode());
  await page.pdf({
    path: output,
    width: "841mm",
    height: "594mm",
    margin: { top: "0", right: "0", bottom: "0", left: "0" },
    printBackground: true,
    preferCSSPageSize: true,
  });
  await page.close();
  console.log(`${pathToFileURL(input).href} -> ${output}`);
} finally {
  await browser.close();
}
