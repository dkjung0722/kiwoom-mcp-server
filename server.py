"""
키움 + 토스 통합 MCP 서버 (HTTP 모드)

[키움] 국내 주식 시세 · 키움 계좌(보유종목·손익) · 예수금
[토스] 환율 · 국내+미국 주식 시세 · 토스 계좌(보유·매수가능) · 캔들 · 장운영
        ※ 주문(매매)은 안전상 제외 — 조회 전용

────────────────────────────────────────────────
상황별 도구 선택 가이드 (Claude가 자동 라우팅)
  • 국내 주식 시세       → get_stock_price (키움)  /  get_global_stock_price (토스)
  • 미국 주식 시세       → get_global_stock_price (토스) ※ 키움은 국내 전용
  • 환율 (USD/KRW 등)    → get_exchange_rate (토스) ※ 키움엔 없음
  • 키움 계좌 보유·손익  → get_account_balance
  • 토스 계좌 보유·손익  → get_toss_holdings (먼저 get_toss_accounts로 account_seq 확인)
────────────────────────────────────────────────
"""

import os
from fastmcp import FastMCP
from kiwoom_client import KiwoomClient
from toss_client import TossClient

from dotenv import load_dotenv
load_dotenv()

mcp = FastMCP("Kiwoom+Toss Stock Server")

kiwoom = KiwoomClient()
toss = TossClient()


# ══════════════════════════════════════════════════════════
#  키움 — 국내 주식 + 키움 계좌
# ══════════════════════════════════════════════════════════
@mcp.tool()
def get_stock_price(stock_code: str) -> dict:
    """한국 주식 또는 ETF의 현재가 및 시세 정보를 조회합니다. (키움)

    국내 종목 시세는 키움이 1차입니다. 미국 주식은 get_global_stock_price를 쓰세요.

    Args:
        stock_code: 6자리 종목 코드.
                    예: '005930'(삼성전자), '360200'(ACE 미국S&P500)
    """
    return kiwoom.get_stock_price(stock_code)


@mcp.tool()
def get_multiple_stock_prices(stock_codes: list[str]) -> list[dict]:
    """여러 국내 종목의 시세를 한 번에 조회합니다. (키움)

    Args:
        stock_codes: 종목 코드 리스트. 예: ['005930', '360200', '367380']
    """
    return kiwoom.get_multiple_stock_prices(stock_codes)


@mcp.tool()
def get_account_balance() -> dict:
    """내 키움 계좌의 보유종목, 평가금액, 평가손익, 수익률을 조회합니다. (kt00018)

    국내 주식 위주의 키움 계좌 현황입니다. 토스 계좌는 get_toss_holdings를 쓰세요.
    """
    return kiwoom.get_account_balance()


@mcp.tool()
def get_deposit() -> dict:
    """내 키움 계좌의 예수금 및 주문가능금액을 조회합니다. (kt00001)"""
    return kiwoom.get_deposit()


# ══════════════════════════════════════════════════════════
#  토스 — 환율 + 국내/미국 시세 + 토스 계좌
# ══════════════════════════════════════════════════════════
@mcp.tool()
def get_exchange_rate(base_currency: str = "USD", quote_currency: str = "KRW") -> dict:
    """환율을 조회합니다. (토스 — 키움에는 없는 기능)

    Args:
        base_currency: 기준 통화 (예: 'USD')
        quote_currency: 상대 통화 (예: 'KRW')
    Returns:
        rate(현재 환율), mid_rate(매매기준율), change_type(UP/DOWN) 등
    """
    return toss.get_exchange_rate(base_currency, quote_currency)


@mcp.tool()
def get_global_stock_price(symbols: str) -> list[dict]:
    """국내·미국 주식의 현재가를 조회합니다. (토스)

    미국 주식(AAPL, NVDA 등)은 이 도구를 사용하세요. 키움은 국내 전용입니다.

    Args:
        symbols: 콤마로 구분. 국내는 6자리(005930), 미국은 티커(AAPL).
                 예: '005930,AAPL,NVDA'
    """
    return toss.get_price(symbols)


@mcp.tool()
def get_candles(symbol: str, interval: str = "1d", count: int = 100) -> dict:
    """종목의 캔들(OHLCV) 차트를 조회합니다. (토스, 국내·미국)

    Args:
        symbol: 국내 6자리 또는 미국 티커
        interval: '1d'(일봉) 또는 '1m'(분봉)
        count: 조회 봉 수 (최대 200)
    """
    return toss.get_candles(symbol, interval, count)


@mcp.tool()
def get_market_calendar(country: str = "KR") -> dict:
    """장 운영 정보(휴장일 등)를 조회합니다. (토스)

    Args:
        country: 'KR'(국내) 또는 'US'(미국)
    """
    return toss.get_market_calendar(country)


@mcp.tool()
def get_toss_accounts() -> list[dict]:
    """내 토스 계좌 목록을 조회합니다. (토스)

    토스 계좌 보유종목·매수가능금액 조회에 필요한 account_seq를 여기서 확인합니다.
    """
    return toss.get_accounts()


@mcp.tool()
def get_toss_holdings(account_seq: int, symbol: str = None) -> dict:
    """내 토스 계좌의 보유종목·평가손익·수익률을 조회합니다. (토스)

    국내+미국 보유분이 원화/달러로 분리되어 나옵니다.
    account_seq를 모르면 먼저 get_toss_accounts를 호출하세요.

    Args:
        account_seq: get_toss_accounts로 확인한 계좌 번호
        symbol: (선택) 특정 종목만 필터
    """
    return toss.get_holdings(account_seq, symbol)


@mcp.tool()
def get_toss_buying_power(account_seq: int, currency: str = "KRW") -> dict:
    """내 토스 계좌의 매수 가능 금액을 조회합니다. (토스)

    Args:
        account_seq: get_toss_accounts로 확인한 계좌 번호
        currency: 'KRW' 또는 'USD'
    """
    return toss.get_buying_power(account_seq, currency)


# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "http":
        port = int(os.getenv("PORT", "8000"))
        print(f"▶ 키움+토스 통합 서버 HTTP 모드 시작 — 포트 {port}")
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        print("▶ stdio 모드로 시작")
        mcp.run()
