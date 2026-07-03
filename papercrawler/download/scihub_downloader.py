"""
Sci-Hub 下载封装器 — Playwright 版本(2026 重构)

⚠️  法律免责声明 / Legal Disclaimer ⚠️
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sci-Hub 未经版权持有人授权分发学术论文。在美国、欧盟及部分其他
地区,访问 Sci-Hub 可能构成版权侵权。多家主要出版商已在美国法院
获得针对 Sci-Hub 的默认判决。

请在使用本模块前:
  1. 了解并遵守您所在地区的版权法律;
  2. 确认您的机构网络使用政策;
  3. 优先通过合法渠道(Unpaywall、PubMed Central、机构图书馆等)
     获取论文全文。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

历史 / 为什么重写
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2024 年之前:Sci-Hub 用 Cloudflare 简单反爬,scidownl 库够用。
2025 年后:Sci-Hub 切到 **DDoS-Guard**(第一层)+ **ALTCHA** PoW(第二层,搜索时触发),
            scidownl 用 `POST + form['request']` 拿到的永远只是反爬中间页
            (7276 字节,标题 "你是机器人吗?"),根本到不了搜索结果页。

本模块用 **Playwright + 真实浏览器**(Edge / Chrome)绕过:
  - DDoS-Guard:真实浏览器自动通过(JS 跑得动)
  - ALTCHA PoW:浏览器 Web Crypto API 算出 nonce,1-3 秒完成
  - PDF URL:从论文详情页的 `<object data="...pdf">` 标签拿到
"""
from __future__ import annotations

import asyncio
import binascii
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from loguru import logger


# ============================================================================
# 浏览器自动探测
# ============================================================================

# Windows 上常见浏览器路径(优先级排序)
_BROWSER_CANDIDATES = [
    # Edge(Win10/11 自带)
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Chrome
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    # Chromium
    r"C:\Program Files\Chromium\Application\chromium.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]


def _find_browser() -> Optional[str]:
    """自动找一个可用的浏览器路径"""
    # 1. 环境变量优先
    env_path = os.environ.get("PAPERCRAWLER_BROWSER_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    # 2. PATH 里找
    for cmd in ("msedge", "chrome", "chromium", "google-chrome", "chromium-browser"):
        p = shutil.which(cmd)
        if p:
            return p
    # 3. 常见路径遍历
    for p in _BROWSER_CANDIDATES:
        if Path(p).exists():
            return p
    return None


# ============================================================================
# 默认 Sci-Hub 镜像
# ============================================================================

DEFAULT_MIRROR = "https://sci-hub.st"


class SciHubDownloader:
    """
    通过 Playwright + 真实浏览器绕过 DDoS-Guard + ALTCHA 反爬,
    下载 Sci-Hub 上的论文 PDF。

    工作流程(每次 download 调用):
      1. 打开 Sci-Hub 首页(DDoS-Guard 自动通过)
      2. 填 textarea + 点击提交 → 触发 ALTCHA 反爬
      3. 等待 <altcha-widget> 出现 → JS 调用 widget.verify()
         → 浏览器 Web Crypto API 算 PoW nonce,提交到 server
      4. 跳到论文详情页 → 找 <object data="*.pdf"> 或 <a href="*.pdf">
      5. 用 Playwright cookie + httpx 拉 PDF 字节流
      6. 写到目标路径

    性能优化:
      - 浏览器实例单例化(`_get_browser`),同一进程多次 download 复用,
        避免每篇论文重新启动(~2-3s 启动开销)。
      - 同一个 SciHubDownloader 实例是线程/协程安全的吗?
        Playwright 的 sync_api 不是,async_api 是。这里用 async 实现,
        每个 instance 持有一个 event loop 的 browser。

    配置:
      proxy   : Playwright 的 proxy 参数,如 "socks5://127.0.0.1:7890"
      mirror  : Sci-Hub 镜像 URL,默认 https://sci-hub.st
      browser : 浏览器可执行文件路径,默认自动检测
      headless: 是否 headless 模式,默认 True(headless=True 通常够用)
      timeout : 单步超时秒数,默认 60
    """

    def __init__(
        self,
        proxy: str = "",
        mirror: str = DEFAULT_MIRROR,
        browser: Optional[str] = None,
        headless: bool = True,
        timeout: int = 60,
    ):
        self.proxy = proxy
        self.mirror = mirror.rstrip("/")
        self.browser_path = browser or _find_browser()
        self.headless = headless
        self.timeout = timeout

        # 浏览器实例(懒加载,首次 download 时启动)
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    @staticmethod
    def is_available() -> bool:
        """检查 playwright + 系统浏览器是否都可用"""
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return _find_browser() is not None

    def status(self) -> str:
        """返回当前可用性状态(给日志用)"""
        try:
            import playwright  # noqa: F401
            has_pw = True
        except ImportError:
            has_pw = False
        bp = self.browser_path or _find_browser()
        return (
            f"playwright={'✓' if has_pw else '✗'} "
            f"browser={bp or '✗ 未找到'} "
            f"mirror={self.mirror}"
        )

    # ------------------------------------------------------------------
    # 浏览器生命周期
    # ------------------------------------------------------------------

    async def _get_browser(self):
        """获取浏览器实例(懒加载 + 复用)"""
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser

            if not self.browser_path:
                raise RuntimeError(
                    "未找到可用浏览器。请安装 Edge/Chrome/Chromium,"
                    "或设置环境变量 PAPERCRAWLER_BROWSER_PATH"
                )

            from playwright.async_api import async_playwright

            launch_kwargs = {
                "executable_path": self.browser_path,
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            }
            if self.proxy:
                # Playwright proxy 格式: {"server": "socks5://..."} 或带 username/password
                launch_kwargs["proxy"] = {"server": self.proxy}

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            logger.info(f"[scihub] 浏览器已启动: {self.browser_path}")
            return self._browser

    async def close(self):
        """关闭浏览器(释放资源)"""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                # 浏览器关闭时可能抛 OSError / playwright Error
                logger.opt(exception=True).debug(f"[scihub] 关闭浏览器失败(忽略): {e}")
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.opt(exception=True).debug(f"[scihub] 停止 playwright 失败(忽略): {e}")
            self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ------------------------------------------------------------------
    # 核心下载流程
    # ------------------------------------------------------------------

    async def download(
        self,
        doi: str,
        dest_dir: str | Path,
        filename: str = "paper.pdf",
    ) -> bool:
        """
        通过 Sci-Hub 下载指定 DOI 的论文 PDF。

        流程:打开 Sci-Hub → 填 DOI → 触发 ALTCHA → verify → 拿 PDF URL → 下载。

        Args:
            doi: 论文 DOI(如 "10.1038/nature12373")
            dest_dir: 目标目录
            filename: 保存的文件名

        Returns:
            True 表示下载成功,False 表示失败
        """
        if not doi or not doi.strip():
            logger.debug("[scihub] DOI 为空,跳过")
            return False

        return await self._fetch_via_browser(
            keyword=doi.strip(), paper_type="doi", dest_dir=dest_dir, filename=filename,
        )

    async def download_by_title(
        self,
        title: str,
        dest_dir: str | Path,
        filename: str = "paper.pdf",
    ) -> bool:
        """
        通过 Sci-Hub 按 title 二次抓取(DOI 失败后的兜底)。

        Args:
            title: 论文完整标题
            dest_dir: 目标目录
            filename: 保存的文件名

        Returns:
            True 表示下载成功,False 表示失败
        """
        if not title or not title.strip():
            logger.debug("[scihub] title 为空,跳过")
            return False
        return await self._fetch_via_browser(
            keyword=title.strip(), paper_type="title", dest_dir=dest_dir, filename=filename,
        )

    async def _fetch_via_browser(
        self, keyword: str, paper_type: str, dest_dir: str | Path, filename: str,
    ) -> bool:
        """统一的浏览器抓取流程"""
        try:
            browser = await self._get_browser()
        except RuntimeError as e:
            # _get_browser 内部找不到浏览器时会抛 RuntimeError
            logger.error(f"[scihub] 启动浏览器失败: {e}")
            return False
        except Exception as e:
            # Playwright 启动失败(权限 / 端口占用等)
            logger.opt(exception=True).error(f"[scihub] 启动浏览器异常: {e}")
            return False

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_path = dest_dir / filename

        # 清掉可能的旧文件
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError as e:
                logger.opt(exception=True).debug(f"[scihub] 清理旧文件失败(忽略): {e}")

        try:
            # 每个 download 用独立 context(独立 cookie/storage)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
                ),
                locale="en-US",
            )
            # 反自动化检测
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()

            # 1. 打开 Sci-Hub 首页(DDoS-Guard 自动过)
            logger.info(f"[scihub {paper_type}] GET {self.mirror}")
            try:
                await page.goto(self.mirror, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except asyncio.TimeoutError as e:
                logger.warning(f"[scihub] 首页 GET 超时: {e}")
                await context.close()
                return False
            except Exception as e:
                # Playwright 网络/协议异常
                logger.opt(exception=True).warning(f"[scihub] 首页 GET 失败: {e}")
                await context.close()
                return False

            # 等首页真正加载完成(DDoS-Guard JS 跑完通常 2-3s)
            await asyncio.sleep(2)

            # 2. 填 textarea + 点击提交
            try:
                await page.wait_for_selector('textarea[name="request"]', timeout=10000)
            except (asyncio.TimeoutError, Exception) as e:
                # asyncio.TimeoutError 或 Playwright 找不到 selector
                logger.warning(f"[scihub] 没找到搜索框,Sci-Hub 页面结构可能变了: {type(e).__name__}")
                await context.close()
                return False

            logger.info(f"[scihub {paper_type}] 提交关键词: {keyword[:60]}")
            await page.fill('textarea[name="request"]', keyword)
            await page.click('button[type="submit"]')

            # 3. 等 altcha-widget 出现 + Svelte custom element 完全 upgrade
            widget_appeared = False
            for _ in range(15):
                if await page.evaluate("!!document.querySelector('altcha-widget')"):
                    widget_appeared = True
                    break
                await asyncio.sleep(1)
            if not widget_appeared:
                logger.warning("[scihub] altcha-widget 没出现")
                await context.close()
                return False

            # 4. JS 调用 widget.verify() — 触发 ALTCHA PoW
            #    浏览器 Web Crypto API 算 nonce,1-3s 完成
            #    需要等 widget 的 verify() 方法 ready(Svelte 升级有延迟)
            logger.info("[scihub] 触发 ALTCHA verify (浏览器算 PoW)")
            verify_result = await page.evaluate(
                """async () => {
                    const w = document.querySelector('altcha-widget');
                    if (!w) return 'no-widget';
                    // 等待 widget 完全 upgrade(verify 方法 ready)
                    let attempts = 0;
                    while (typeof w.verify !== 'function' && attempts < 50) {
                        await new Promise(r => setTimeout(r, 100));
                        attempts++;
                    }
                    if (typeof w.verify !== 'function') {
                        return 'verify-not-ready-' + attempts;
                    }
                    try {
                        const t0 = Date.now();
                        await w.verify();
                        return 'ok-' + (Date.now() - t0) + 'ms';
                    } catch (e) {
                        return 'err:' + e.message;
                    }
                }"""
            )
            logger.info(f"[scihub] verify 结果: {verify_result}")
            if verify_result.startswith("err") or verify_result.startswith("verify-not-ready"):
                await context.close()
                return False

            # 5. 等页面跳转(URL 变成 sci-hub.st/<doi>)
            #    ALTCHA 通过后会触发 form submit 跳转
            paper_page_loaded = False
            for _ in range(15):
                url = page.url
                # 论文详情页 URL 是 https://sci-hub.st/<doi> 或者 https://sci-hub.st/<title_slug>
                if url.rstrip("/") != self.mirror.rstrip("/") and "/captcha/" not in url:
                    paper_page_loaded = True
                    logger.debug(f"[scihub] 跳到: {url}")
                    break
                await asyncio.sleep(1)

            if not paper_page_loaded:
                logger.warning("[scihub] 论文页未跳转")
                await context.close()
                return False

            # 给页面 1-2s 加载 PDF iframe / object
            await asyncio.sleep(2)

            # 6. 找 PDF URL
            pdf_url = await page.evaluate(
                """() => {
                    // 优先 <object data>
                    const obj = document.querySelector('object[data]');
                    if (obj && /\\.pdf/i.test(obj.data || '')) return obj.data;
                    // 然后 <embed src>
                    const embed = document.querySelector('embed[src]');
                    if (embed && /\\.pdf/i.test(embed.src || '')) return embed.src;
                    // 最后 <iframe src>
                    const iframe = document.querySelector('iframe[src]');
                    if (iframe && /\\.pdf/i.test(iframe.src || '')) return iframe.src;
                    // a[href*=.pdf]
                    const a = document.querySelector('a[href*=".pdf"]');
                    if (a) return a.href;
                    return null;
                }"""
            )

            if not pdf_url:
                # 可能 PDF URL 不含 .pdf 后缀(Sci-Hub 有时用 #navpanes=0 后缀)
                # 也可能在某个 iframe 里(Sci-Hub 新版)
                logger.warning("[scihub] 没找到 PDF URL,看下页面所有资源")
                # 退而求其次:拿当前页面的所有 URL,找像 PDF 的
                all_urls = await page.evaluate(
                    """() => {
                        const urls = new Set();
                        document.querySelectorAll('[src],[data],[href]').forEach(el => {
                            ['src','data','href'].forEach(a => {
                                const v = el.getAttribute(a);
                                if (v && /^https?:\\/\\//.test(v)) urls.add(v);
                            });
                        });
                        return Array.from(urls);
                    }"""
                )
                for u in all_urls:
                    if "storage" in u and (".pdf" in u.lower() or u.endswith(("#navpanes=0&view=FitH", "#navpanes=0"))):
                        pdf_url = u
                        break

            if not pdf_url:
                title = await page.title()
                logger.warning(f"[scihub] 论文页无 PDF, title: {title[:80]}")
                await context.close()
                return False

            # 7. 用浏览器内 fetch() 拉 PDF(用 page 上的 fetch,带完整浏览器指纹)
            logger.info(f"[scihub] PDF URL: {pdf_url[:100]}")
            pdf_bytes = await self._fetch_pdf_via_context(context, pdf_url, page=page)
            if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
                logger.warning(
                    f"[scihub] 拉到的不是 PDF (头 {len(pdf_bytes or b'')} 字节): "
                    f"{(pdf_bytes or b'')[:30]!r}"
                )
                await context.close()
                return False

            # 8. 写文件
            target_path.write_bytes(pdf_bytes)
            logger.success(
                f"[scihub] 下载成功: {target_path.name} ({len(pdf_bytes)} bytes) "
                f"[{paper_type}: {keyword[:40]}]"
            )
            await context.close()
            return True

        except Exception as e:
            # 兜底:Playwright / asyncio / 业务逻辑异常,带 traceback
            logger.opt(exception=True).error(f"[scihub] 抓取异常: {e}")
            return False

    async def _fetch_pdf_via_context(self, context, pdf_url: str, page=None) -> Optional[bytes]:
        """
        用浏览器内的 fetch() 拉 PDF 字节。

        关键洞察:
          - ``context.request.get`` / ``httpx`` / ``requests`` 都走底层 HTTP API,
            没有完整浏览器指纹,Sci-Hub 的 DDoS-Guard 二次拦截(返回 403 + browser check 页)
          - 只有 ``page.evaluate(fetch(...))`` 在浏览器进程里跑,带完整指纹,
            才能拿到 PDF(实测 4.6 MB vs 898 字节 HTML)

        为什么不在 page.goto 拉?
          - page.goto PDF URL 会触发 Chromium 内置 PDF viewer,得到的是渲染页不是字节
        """
        try:
            # 优先用传进来的 page;否则新建一个 context page
            if page is None:
                page = await context.new_page()

            # 在浏览器里用 fetch() 拉,带完整指纹
            # PDF URL 里的 fragment 浏览器自动忽略
            result = await page.evaluate(
                """async (url) => {
                    try {
                        const r = await fetch(url, {
                            credentials: 'include',
                            headers: { 'Accept': 'application/pdf,*/*' }
                        });
                        if (!r.ok) {
                            return { err: 'status-' + r.status, ct: r.headers.get('content-type') };
                        }
                        const ct = r.headers.get('content-type') || '';
                        const buf = await r.arrayBuffer();
                        // 转 base64 给 Python
                        const bytes = new Uint8Array(buf);
                        let bin = '';
                        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                        return { ok: true, ct: ct, len: bytes.length, b64: btoa(bin) };
                    } catch (e) {
                        return { err: e.message };
                    }
                }""",
                pdf_url,
            )
            if "err" in result:
                logger.warning(
                    f"[scihub] fetch failed: {result['err']}, "
                    f"ct={result.get('ct', '?')}"
                )
                return None

            import base64
            try:
                body = base64.b64decode(result["b64"])
            except (binascii.Error, ValueError, TypeError) as e:
                # binascii.Error: 非 base64;ValueError: 长度错;TypeError: 输入不是 bytes-like
                logger.opt(exception=True).warning(f"[scihub] base64 decode failed: {e}")
                return None

            return body

        except Exception as e:
            # 兜底:Playwright page.evaluate 异常
            logger.opt(exception=True).warning(f"[scihub] PDF 拉取异常: {e}")
            return None


# ============================================================================
# 便捷接口(向后兼容 scidownl 风格的同步调用)
# ============================================================================

def is_available() -> bool:
    """便捷接口:检查 SciHubDownloader 是否可用"""
    return SciHubDownloader.is_available()