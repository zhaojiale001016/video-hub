"""带登录态拉起真实浏览器直达创作平台页。

video-hub 平台中枢面板"登录态打开"调用:
    open_creator.py <storage_state.json> <url>

cookie 文件即 sau cookies/<platform>_<account>.json(playwright storage_state
格式)。关窗时把最新会话回写 cookie 文件, 续命登录态。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from patchright.async_api import async_playwright


async def main() -> int:
    cookie_file, url = sys.argv[1], sys.argv[2]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False, channel="chromium",
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(storage_state=cookie_file)
        page = await ctx.new_page()
        await page.goto(url)
        try:
            await ctx.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            await ctx.storage_state(path=cookie_file)   # 回写最新登录态
        except Exception:
            pass
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
