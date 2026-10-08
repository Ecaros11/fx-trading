"""
币安只读客户端
==============
· 从 .env 读取密钥（值不会出现在任何输出里）
· 自动走本地代理 127.0.0.1:1080
· 支持现货(api)和 U 本位合约(fapi)
· 只实现【读取类】接口

用法：
    from binance_api import BN
    bn = BN()
    bn.account()          # 合约账户
    bn.positions()        # 持仓
"""
import hashlib
import hmac
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ENV_PATH = pathlib.Path(__file__).parent / ".env"
PROXY = "http://127.0.0.1:1080"
SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"


def load_env(path=ENV_PATH):
    """手动解析 .env，不依赖 python-dotenv"""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def mask(s, keep=4):
    """任何要打印的敏感串都过这个函数"""
    if not s:
        return "(空)"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "…" + s[-2:]


class BNError(Exception):
    pass


class BN:
    def __init__(self, proxy=None, timeout=40, recv_window=60000):
        env = load_env()
        self.key = env.get("BINANCE_KEY") or env.get("BINANCE_API_KEY") or env.get("API_KEY")
        self.secret = (env.get("BINANCE_SECRET") or env.get("BINANCE_API_SECRET")
                       or env.get("SECRET_KEY") or env.get("API_SECRET"))
        if not self.key or not self.secret:
            raise BNError("在 {} 里没找到密钥".format(ENV_PATH))
        if proxy is None:
            proxy = env.get("BINANCE_PROXY") or PROXY
        if proxy:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            self.opener = urllib.request.build_opener()
        self.timeout = timeout
        self.recv_window = recv_window
        self.key_hint = mask(self.key)
        # 关键：代理有十几秒延迟，必须做时间同步
        self._offset_ms = 0.0
        self.sync_time()

    # ---------------------------------------------------------------- 时间同步
    def sync_time(self):
        """
        取服务器时间，算出本机与币安的偏差。
        代理有 ~13 秒的往返延迟，不做这个同步，签名时间戳必然过期 (-1021)。
        """
        try:
            sent = time.time() * 1000
            r = self._request("GET", FAPI + "/fapi/v1/time", signed=False, _no_sync=True)
            # 简单估计：假设延迟对称，服务器时间大约在 (发出+收到)/2
            received = time.time() * 1000
            self._offset_ms = r["serverTime"] - (sent + received) / 2
            return self._offset_ms
        except Exception:
            self._offset_ms = 0.0
            return 0.0

    def _ts(self):
        return int(time.time() * 1000 + self._offset_ms)

    # ---------------------------------------------------------------- 底层
    def _request(self, method, url, params=None, signed=True, headers=None,
                 _no_sync=False, _retry=True):
        if method != "GET":
            raise BNError("此客户端只允许 GET 读取请求")
        params = dict(params or {})
        if signed:
            params["timestamp"] = self._ts()
            params.setdefault("recvWindow", self.recv_window)
        qs = urllib.parse.urlencode(params)
        h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        if headers:
            h.update(headers)
        if signed:
            sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            qs = qs + "&signature=" + sig
            h["X-MBX-APIKEY"] = self.key
        full = url + ("?" + qs if qs else "")
        try:
            r = self.opener.open(urllib.request.Request(full, headers=h), timeout=self.timeout)
            return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # -1021 = 时间戳过期 → 重新同步一次再试
            if _retry and "-1021" in body and not _no_sync:
                self.sync_time()
                return self._request(method, url, params, signed, headers,
                                     _no_sync=False, _retry=False)
            raise BNError("HTTP {} {} → {}".format(e.code, url, body[:200])) from None
        except Exception as e:
            raise BNError("{}: {}".format(type(e).__name__, str(e)[:120])) from None

    def spot(self, path, params=None, signed=True):
        return self._request("GET", SPOT + path, params, signed)

    def fapi(self, path, params=None, signed=True):
        return self._request("GET", FAPI + path, params, signed)

    # ---------------------------------------------------------------- 只读接口
    def ping(self):
        """连通性（不需要签名）"""
        t0 = time.time()
        a = self.spot("/api/v3/time", signed=False)
        b = self.fapi("/fapi/v1/time", signed=False)
        return {"spot_ok": "serverTime" in a, "fapi_ok": "serverTime" in b,
                "ms": int((time.time() - t0) * 1000)}

    def permissions(self):
        """这个 key 开了哪些权限 —— 最重要的一项检查"""
        return self.spot("/sapi/v1/account/apiRestrictions")

    def futures_account(self):
        return self.fapi("/fapi/v2/account")

    def futures_balance(self):
        return self.fapi("/fapi/v2/balance")

    def positions(self, symbol=None):
        p = {"symbol": symbol} if symbol else {}
        return self.fapi("/fapi/v2/positionRisk", p)

    def open_orders(self, symbol=None):
        p = {"symbol": symbol} if symbol else {}
        return self.fapi("/fapi/v1/openOrders", p)

    # ---------------------------------------------------------------- Algo 委托
    # 关键：通过 App/网页界面挂的「止盈止损」属于 Algo Order（条件委托），
    #       不会出现在 openOrders / allOrders 里，必须查这一组接口。
    def open_algo_orders(self, symbol=None):
        """当前所有 Algo 委托（就是界面上的『条件委托』）"""
        p = {"symbol": symbol} if symbol else {}
        return self.fapi("/fapi/v1/openAlgoOrders", p)

    def all_algo_orders(self, symbol=None, limit=100, **kw):
        p = {"limit": limit}
        if symbol:
            p["symbol"] = symbol
        p.update(kw)
        return self.fapi("/fapi/v1/allAlgoOrders", p)

    def algo_order(self, algo_id=None, client_algo_id=None):
        p = {}
        if algo_id is not None:
            p["algoId"] = algo_id
        if client_algo_id is not None:
            p["clientAlgoId"] = client_algo_id
        return self.fapi("/fapi/v1/algoOrder", p)

    def all_orders_combined(self, symbol=None):
        """
        把『基础单』和『条件委托』合并成一个列表 —— 这样才不会漏。
        返回 [(来源, 订单字典), ...]
        """
        out = []
        for tag, fn in (("基础单", self.open_orders), ("条件委托", self.open_algo_orders)):
            try:
                for o in fn(symbol):
                    out.append((tag, o))
            except BNError:
                pass
        return out

    def income(self, symbol=None, limit=1000, **kw):
        """
        资金流水（转账、手续费、资金费、已实现盈亏）。

        ⚠️ 2026-10-08 修复两处：
          ① 原来默认 symbol="BTCUSDT" —— 但本项目交易的是 ETHUSDT，
             忘记传 symbol 就会【静默】拿到 BTC 的数据（实测：
             传 BTCUSDT 得 30 条 / 峰值 2.67，不传得 145 条 / 峰值 90.85）。
             ⇒ 改为默认 None（= 返回【所有合约】的流水，最不容易搞错）。
          ② 原来调 self.fapi(path, p) 没传 signed=True，而这个接口
             【需要签名】（查自己的流水）⇒ 之前可能一直没正常工作。
             ⇒ 现在显式 signed=True。
        """
        p = {"limit": limit}
        if symbol:
            p["symbol"] = symbol
        p.update(kw)
        return self.fapi("/fapi/v1/income", p, signed=True)

    def all_income(self, max_pages=20, **kw):
        """
        分页拉取【全部】资金流水（不传 symbol ⇒ 覆盖所有合约）。

        ⚠️ 为什么要分页：单次上限 1000 条，账户跑久了会超过。
        ⚠️ 分页用 startTime 递增 —— 币安的 income 接口按时间升序返回，
           所以用最后一条的时间 +1ms 作为下一页的起点。
        """
        rows, start = [], None
        for _ in range(max_pages):
            p = {"limit": 1000}
            if start is not None:
                p["startTime"] = start
            p.update(kw)
            r = self.fapi("/fapi/v1/income", p, signed=True)
            if not r:
                break
            rows.extend(r)
            if len(r) < 1000:
                break
            start = int(r[-1]["time"]) + 1
        return rows

    def user_trades(self, symbol="BTCUSDT", limit=50, **kw):
        p = {"symbol": symbol, "limit": limit}
        p.update(kw)
        return self.fapi("/fapi/v1/userTrades", p)
