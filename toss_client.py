"""
토스증권 Open API 클라이언트 (조회 전용)
- 토큰 자동 발급 및 캐싱
- 국내·미국 주식 현재가 (/api/v1/prices)
- 환율 조회 (/api/v1/exchange-rate)  ← 키움에 없던 기능
- 캔들/종목정보/장운영달력
- 계좌 목록 / 보유주식 / 매수가능금액

[안전] 주문(생성·정정·취소) 엔드포인트는 의도적으로 구현하지 않음.

[키움과의 차이]
  - 인증: client_credentials + Bearer (토스는 토큰 응답에 expires_in 초 단위)
  - 계좌·자산 계열(holdings, buying-power)은 X-Tossinvest-Account 헤더 필요
  - 숫자가 전부 문자열로 옴("72000","1380.5","0.1077") → 변환 필요
  - 손익률(rate)은 소수(0.1077 = 10.77%) → ×100 해서 % 변환
"""

import os
import time
import requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

TOSS_BASE = "https://openapi.tossinvest.com"


def _f(value) -> float:
    """토스 응답의 문자열 숫자를 float로."""
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


class TossClient:
    """토스증권 Open API 클라이언트 (조회 전용)"""

    def __init__(self):
        self.client_id = os.getenv("TOSS_CLIENT_ID")
        self.client_secret = os.getenv("TOSS_CLIENT_SECRET")
        if not all([self.client_id, self.client_secret]):
            raise ValueError("환경변수 누락: TOSS_CLIENT_ID, TOSS_CLIENT_SECRET 확인")
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

    # ------------------------------------------------------------------ #
    # 토큰
    # ------------------------------------------------------------------ #
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        resp = requests.post(
            f"{TOSS_BASE}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._token

    # ------------------------------------------------------------------ #
    # 공통 GET
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: Optional[dict] = None,
             account_seq: Optional[int] = None, raw: bool = False):
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)  # 계좌·자산 계열 필수
        resp = requests.get(f"{TOSS_BASE}{path}", headers=headers, params=params or {})
        resp.raise_for_status()
        data = resp.json()
        return data if raw else data.get("result")

    # ------------------------------------------------------------------ #
    # 시세 (국내·미국)
    # ------------------------------------------------------------------ #
    def get_price(self, symbols, raw: bool = False):
        """국내(6자리)·미국(티커) 현재가. symbols: 'AAPL' 또는 ['005930','AAPL']"""
        if isinstance(symbols, list):
            symbols = ",".join(symbols)
        result = self._get("/api/v1/prices", {"symbols": symbols}, raw=raw)
        if raw:
            return result
        return [{
            "symbol": r.get("symbol"),
            "price": _f(r.get("lastPrice")),
            "currency": r.get("currency"),
            "timestamp": r.get("timestamp"),
        } for r in result]

    # ------------------------------------------------------------------ #
    # 환율  ★ 키움에 없던 기능
    # ------------------------------------------------------------------ #
    def get_exchange_rate(self, base_currency: str = "USD",
                          quote_currency: str = "KRW", raw: bool = False):
        """환율 조회. 예: USD→KRW"""
        r = self._get("/api/v1/exchange-rate",
                      {"baseCurrency": base_currency, "quoteCurrency": quote_currency},
                      raw=raw)
        if raw:
            return r
        return {
            "base": r.get("baseCurrency"),
            "quote": r.get("quoteCurrency"),
            "rate": _f(r.get("rate")),               # 현재 환율
            "mid_rate": _f(r.get("midRate")),        # 매매기준율
            "change_type": r.get("rateChangeType"),  # UP / DOWN
            "valid_from": r.get("validFrom"),
            "valid_until": r.get("validUntil"),
        }

    # ------------------------------------------------------------------ #
    # 캔들 차트
    # ------------------------------------------------------------------ #
    def get_candles(self, symbol: str, interval: str = "1d",
                    count: int = 100, raw: bool = False):
        """캔들(OHLCV). interval: '1m' 또는 '1d'"""
        return self._get("/api/v1/candles",
                         {"symbol": symbol, "interval": interval, "count": count},
                         raw=raw)

    # ------------------------------------------------------------------ #
    # 종목 기본정보
    # ------------------------------------------------------------------ #
    def get_stock_info(self, symbols, raw: bool = False):
        if isinstance(symbols, list):
            symbols = ",".join(symbols)
        return self._get("/api/v1/stocks", {"symbols": symbols}, raw=raw)

    # ------------------------------------------------------------------ #
    # 장 운영 달력 (KR / US)
    # ------------------------------------------------------------------ #
    def get_market_calendar(self, country: str = "KR", raw: bool = False):
        country = country.upper()
        return self._get(f"/api/v1/market-calendar/{country}", raw=raw)

    # ------------------------------------------------------------------ #
    # 계좌 목록  (accountSeq를 여기서 얻음)
    # ------------------------------------------------------------------ #
    def get_accounts(self, raw: bool = False):
        result = self._get("/api/v1/accounts", raw=raw)
        if raw:
            return result
        return [{
            "account_no": a.get("accountNo"),
            "account_seq": a.get("accountSeq"),
            "account_type": a.get("accountType"),
        } for a in result]

    # ------------------------------------------------------------------ #
    # 보유 주식 (계좌 헤더 필요)
    # ------------------------------------------------------------------ #
    def get_holdings(self, account_seq: int, symbol: Optional[str] = None,
                     raw: bool = False):
        params = {"symbol": symbol} if symbol else None
        r = self._get("/api/v1/holdings", params, account_seq=account_seq, raw=raw)
        if raw:
            return r

        tp = r.get("totalPurchaseAmount", {})
        mv = r.get("marketValue", {}).get("amount", {})
        pl = r.get("profitLoss", {})

        items = []
        for it in r.get("items", []):
            ipl = it.get("profitLoss", {})
            imv = it.get("marketValue", {})
            items.append({
                "symbol": it.get("symbol"),
                "name": it.get("name"),
                "country": it.get("marketCountry"),   # KR / US
                "currency": it.get("currency"),
                "quantity": _f(it.get("quantity")),
                "avg_price": _f(it.get("averagePurchasePrice")),
                "last_price": _f(it.get("lastPrice")),
                "purchase_amount": _f(imv.get("purchaseAmount")),
                "eval_amount": _f(imv.get("amount")),
                "profit_loss": _f(ipl.get("amount")),
                "profit_rate": round(_f(ipl.get("rate")) * 100, 2),  # 0.1077 → 10.77
            })

        return {
            "total_purchase": {"krw": _f(tp.get("krw")), "usd": _f(tp.get("usd"))},
            "market_value": {"krw": _f(mv.get("krw")), "usd": _f(mv.get("usd"))},
            "profit_loss": {
                "krw": _f(pl.get("amount", {}).get("krw")),
                "usd": _f(pl.get("amount", {}).get("usd")),
            },
            "total_profit_rate": round(_f(pl.get("rate")) * 100, 2),  # 원화환산 손익률(%)
            "items": items,
            "holding_count": len(items),
        }

    # ------------------------------------------------------------------ #
    # 매수 가능 금액 (계좌 헤더 필요)
    # ------------------------------------------------------------------ #
    def get_buying_power(self, account_seq: int, currency: str = "KRW",
                         raw: bool = False):
        r = self._get("/api/v1/buying-power", {"currency": currency},
                      account_seq=account_seq, raw=raw)
        if raw:
            return r
        return {
            "currency": r.get("currency"),
            "cash_buying_power": _f(r.get("cashBuyingPower")),
        }


# 모듈 단독 실행 테스트
if __name__ == "__main__":
    client = TossClient()

    print("▶ 토큰 발급 테스트...")
    client._get_token()
    print("✅ 토큰 발급 완료\n")

    print("▶ [환율] USD→KRW")
    fx = client.get_exchange_rate("USD", "KRW")
    print(f"✅ {fx['base']}/{fx['quote']}: {fx['rate']:,.2f} ({fx['change_type']})\n")

    print("▶ [시세] 삼성전자 + Apple")
    for p in client.get_price(["005930", "AAPL"]):
        print(f"   {p['symbol']}: {p['price']:,.2f} {p['currency']}")
    print()

    print("▶ [계좌] 목록")
    accts = client.get_accounts()
    for a in accts:
        print(f"   seq={a['account_seq']} {a['account_type']} {a['account_no']}")

    if accts:
        seq = accts[0]["account_seq"]
        print(f"\n▶ [보유주식] account_seq={seq}")
        h = client.get_holdings(seq)
        print(f"✅ 손익률 {h['total_profit_rate']:+.2f}% / {h['holding_count']}종목")
        for it in h["items"]:
            print(f"   {it['name']}({it['country']}): {it['profit_rate']:+.2f}%")
