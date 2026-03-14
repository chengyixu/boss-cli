"""Browser-assisted QR login via Camoufox.

Opens a real browser window at the Boss Zhipin login page, renders
the QR code in the terminal, and exports all cookies (including
the JS-generated ``__zp_stoken__``) after the user scans and confirms.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

from .auth import Credential, save_credential
from .constants import BASE_URL

logger = logging.getLogger(__name__)

LOGIN_URL = f"{BASE_URL}/web/user/"

# API endpoints intercepted from the browser
QR_RANDKEY_ENDPOINT = "/wapi/zppassport/captcha/randkey"
QR_SCAN_ENDPOINT = "/wapi/zppassport/qrcode/scan"
QR_SCAN_LOGIN_ENDPOINT = "/wapi/zppassport/qrcode/scanLogin"

# Cookie names to export from browser
BROWSER_EXPORT_DOMAINS = (".zhipin.com", "zhipin.com", "www.zhipin.com")

POLL_TIMEOUT_S = 240  # 4 minutes


class BrowserLoginUnavailable(RuntimeError):
    """Raised when the camoufox browser backend cannot be started."""


def _ensure_camoufox_ready() -> None:
    """Validate that the Camoufox package and browser binary are available."""
    try:
        import camoufox  # noqa: F401
    except ImportError as exc:
        raise BrowserLoginUnavailable(
            "Browser-assisted QR login requires the `camoufox` package.\n"
            "Install it with: pip install 'kabi-boss-cli[browser]'"
        ) from exc

    try:
        result = subprocess.run(
            [sys.executable, "-m", "camoufox", "path"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserLoginUnavailable(
            "Unable to validate the Camoufox browser installation."
        ) from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise BrowserLoginUnavailable(
            "Camoufox browser runtime is missing. Run `python -m camoufox fetch` first."
        )


def _normalize_browser_cookies(raw_cookies: list[dict[str, Any]]) -> dict[str, str]:
    """Convert Playwright cookie entries into a flat dict, filtering to zhipin.com."""
    cookies: dict[str, str] = {}
    for entry in raw_cookies:
        name = entry.get("name")
        value = entry.get("value")
        domain = entry.get("domain", "")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not any(domain.endswith(d) for d in BROWSER_EXPORT_DOMAINS):
            continue
        cookies[name] = value
    return cookies


def browser_qr_login(
    *,
    on_status: callable | None = None,
    timeout_s: int = POLL_TIMEOUT_S,
) -> Credential:
    """Log in by letting a real browser complete the QR flow, then export cookies.

    1. Open the Boss Zhipin login page in Camoufox (visible browser window)
    2. Intercept the QR randkey API to get qrId
    3. Render QR in terminal for user to scan
    4. Wait for scan confirmation
    5. Export all cookies from the browser context
    """
    _ensure_camoufox_ready()

    try:
        from camoufox.sync_api import Camoufox
    except ImportError as exc:
        raise BrowserLoginUnavailable(
            "Camoufox sync API is unavailable in the current environment."
        ) from exc

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)
        else:
            print(msg)

    _emit("🔑 正在启动浏览器辅助登录...")

    with Camoufox(headless=False) as browser:
        page = browser.new_page()

        state: dict[str, Any] = {"scanned": False, "confirmed": False, "qr_id": ""}

        def _handle_response(response: Any) -> None:
            """Monitor scan/scanLogin API responses from the browser."""
            url = response.url
            try:
                if QR_SCAN_ENDPOINT in url and "scanLogin" not in url:
                    data = response.json()
                    if data.get("scaned") or data.get("newScaned"):
                        if not state["scanned"]:
                            state["scanned"] = True
                            _emit("  📲 已扫码，请在手机上确认...")
                elif QR_SCAN_LOGIN_ENDPOINT in url:
                    data = response.json()
                    if data.get("login") is True:
                        state["confirmed"] = True
                        _emit("  ✅ 扫码确认成功！")
            except Exception as exc:
                logger.debug("Failed to parse browser response from %s: %s", url, exc)

        page.on("response", _handle_response)

        # Navigate and intercept the randkey API to get qrId
        try:
            with page.expect_response(
                lambda resp: QR_RANDKEY_ENDPOINT in resp.url and resp.request.method == "POST",
                timeout=20_000,
            ) as randkey_info:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
        except Exception as exc:
            raise RuntimeError("无法加载 Boss 直聘登录页面") from exc

        try:
            randkey_data = randkey_info.value.json()
        except Exception as exc:
            raise RuntimeError("无法解析 QR session 响应") from exc

        qr_id = randkey_data.get("zpData", {}).get("qrId", "")
        if not qr_id:
            raise RuntimeError(f"QR session 未返回 qrId: {randkey_data}")

        state["qr_id"] = qr_id

        # Display QR in terminal (best-effort) + tell user about browser window
        _emit("\n📱 请使用 Boss 直聘 APP 扫描二维码登录:\n")
        try:
            from .auth import _display_qr_in_terminal
            _display_qr_in_terminal(qr_id)
        except Exception:
            pass
        _emit(f"\n⏳ 扫码后请在手机上确认登录... (QR ID: {qr_id[:20]}...)")
        _emit("  💡 也可以直接在浏览器窗口中扫描二维码\n")

        # Wait for browser to navigate away from login page (means login done)
        try:
            page.wait_for_url(
                lambda url: "/web/user" not in url,
                timeout=timeout_s * 1000,
            )
        except Exception:
            # Timeout or navigation didn't happen, check if cookies were set anyway
            logger.debug("Browser did not navigate away from login page before timeout")

        # Give the page a moment to settle (JS sets cookies)
        try:
            page.wait_for_timeout(2000)
        except Exception:
            pass

        # Export cookies
        cookies = _normalize_browser_cookies(page.context.cookies())

    if not cookies:
        raise RuntimeError("浏览器登录后未获取到任何 Cookie")

    credential = Credential(cookies=cookies)

    missing = credential.missing_required_cookies
    if missing:
        logger.warning("Browser login missing cookies: %s", ", ".join(missing))

    # Save and return
    save_credential(credential)
    return credential
