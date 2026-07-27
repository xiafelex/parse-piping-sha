#!/usr/bin/env node
/** Rasterize a tree of SHA-derived SVG files with one Playwright browser.
 *
 * This is an artifact renderer only. It does not open or inspect PDF files.
 * Use NODE_PATH when Playwright is supplied by a shared runtime rather than a
 * project-local node_modules directory.
 */

const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

const [inputRoot, outputRoot, widthArgument = "1200"] = process.argv.slice(2);
const width = Number.parseInt(widthArgument, 10);

if (!inputRoot || !outputRoot || !Number.isFinite(width) || width <= 0) {
  console.error("Usage: render_svg_tree_png.cjs <svg-root> <png-root> [width]");
  process.exit(1);
}

async function svgFiles(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const results = [];
  for (const entry of entries) {
    const child = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      results.push(...await svgFiles(child));
    } else if (entry.isFile() && entry.name.endsWith(".svg")) {
      results.push(child);
    }
  }
  return results.sort();
}

async function browserLaunchOptions() {
  if (process.env.SHA_BROWSER_EXECUTABLE) {
    return { headless: true, executablePath: process.env.SHA_BROWSER_EXECUTABLE };
  }
  // Codex desktop images can retain a compatible Chromium binary from a
  // previous Playwright version even when the current package has no browser
  // download. Reuse it before requiring a network browser installation.
  const cache = path.join(process.env.HOME || "", "Library", "Caches", "ms-playwright");
  try {
    const entries = (await fs.readdir(cache, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("chromium_headless_shell-"))
      .sort((left, right) => right.name.localeCompare(left.name));
    for (const entry of entries) {
      const candidate = path.join(cache, entry.name, "chrome-headless-shell-mac-arm64", "chrome-headless-shell");
      try {
        await fs.access(candidate);
        return { headless: true, executablePath: candidate };
      } catch {
        // Try another cached browser or the default Playwright location.
      }
    }
  } catch {
    // The default Playwright launch below emits the actionable install error.
  }
  return { headless: true };
}

async function rasterize(browser, svgPath, pngPath) {
  const page = await browser.newPage({ viewport: { width, height: width } });
  try {
    await page.goto(pathToFileURL(svgPath).href, { waitUntil: "load" });
    const viewBox = await page.locator("svg").evaluate((svg) => {
      const box = svg.viewBox.baseVal;
      return { width: box.width, height: box.height };
    });
    const height = Math.max(1, Math.round(width * viewBox.height / viewBox.width));
    await page.setViewportSize({ width, height });
    await page.locator("svg").evaluate((svg, size) => {
      svg.setAttribute("width", String(size.width));
      svg.setAttribute("height", String(size.height));
      svg.style.display = "block";
      document.documentElement.style.background = "white";
    }, { width, height });
    await fs.mkdir(path.dirname(pngPath), { recursive: true });
    await page.locator("svg").screenshot({ path: pngPath });
  } finally {
    await page.close();
  }
}

async function main() {
  const source = path.resolve(inputRoot);
  const target = path.resolve(outputRoot);
  const files = await svgFiles(source);
  const browser = await chromium.launch(await browserLaunchOptions());
  try {
    for (let index = 0; index < files.length; index += 1) {
      const svgPath = files[index];
      const relative = path.relative(source, svgPath).replace(/\.svg$/, ".png");
      await rasterize(browser, svgPath, path.join(target, relative));
      console.log(`[${index + 1}/${files.length}] ${relative}`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
