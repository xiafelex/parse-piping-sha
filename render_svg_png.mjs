#!/usr/bin/env node
/** Render a complete SHA SVG page to PNG without cropping its oversized canvas. */

import { pathToFileURL } from "node:url";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [input, output, widthArg = "3360"] = process.argv.slice(2);
if (!input || !output) {
  console.error("Usage: node render_svg_png.mjs input.svg output.png [width]");
  process.exit(1);
}

const width = Number.parseInt(widthArg, 10);
if (!Number.isFinite(width) || width <= 0) {
  throw new Error(`Invalid PNG width: ${widthArg}`);
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width, height: width } });
  await page.goto(pathToFileURL(input).href, { waitUntil: "load" });
  const pageSize = await page.evaluate(() => {
    const svg = document.documentElement;
    const viewBox = svg.viewBox.baseVal;
    return { ratio: viewBox.width / viewBox.height };
  });
  const height = Math.round(width / pageSize.ratio);
  await page.close();
  const raster = await browser.newPage({ viewport: { width, height } });
  // Standalone SVG documents retain their root width/height attributes. Put
  // the source in an HTML image box so object-fit, not those attributes,
  // controls the final raster dimensions.
  const sourceUrl = `data:image/svg+xml;base64,${Buffer.from(await readFile(input)).toString("base64")}`;
  await raster.setContent(
    `<html><body><img src="${sourceUrl}" /></body></html>`,
    { waitUntil: "load" },
  );
  await raster.addStyleTag({
    content: "html,body{margin:0;width:100%;height:100%;overflow:hidden;background:white}img{display:block;width:100%;height:100%;object-fit:contain}",
  });
  await raster.locator("img").evaluate((image) => image.decode());
  await raster.screenshot({ path: output });
  await raster.close();
  console.log(`${input} -> ${output} (${width}x${height})`);
} finally {
  await browser.close();
}
