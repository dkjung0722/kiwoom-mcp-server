"""
Kiwoom Stock MCP Server (HTTP 모드)
- 국내 주식/ETF 시세 조회 (ka10001)
- 계좌평가잔고 조회 = 실제 보유종목·평가금액·수익률 (kt00018)  ★핵심 추가
- 예수금 조회 (kt00001)
- 다중 종목 일괄 조회

[KIS 대비 제약]
  키움 REST API는 현재 '국내주식(ETF/ETN 포함)'만 지원합니다.
  → 기존 get_overseas_stock_price(해외 직접주식)는 키움에 없습니다.
    단, 동근님 미국 ETF(ACE S&P500 등)는 국내 상장 ETF라 국내 종목코드로 조회됩니다.
"""

import os
from fastmcp import FastMCP
from kiwoom_client import KiwoomClient

from dotenv import load_dotenv
load_dotenv()

mcp = FastMCP("Kiwoom Stock Server")

# 환경변수 KIWOOM_IS_MOCK로 실전/모의 분기 (검증 끝나기 전엔 모의 권장)
kiwoom = KiwoomClient()


@mcp.tool()
def get_stock_price(stock_code: str) -> dict:
    """한국 주식 또는 ETF의 현재가 및 시세 정보를 조회합니다.

    Args:
        stock_code: 6자리 종목 코드.
                    예: '005930' (삼성전자), '360200' (ACE 미국S&P500),
                        '367380' (ACE 미국나스닥100), '381170' (TIGER 미국테크TOP10)
    """
    return kiwoom.get_stock_price(stock_code)


@mcp.tool()
def get_multiple_stock_prices(stock_codes: list[str]) -> list[dict]:
    """여러 국내 종목의 시세를 한 번에 조회합니다.

    Args:
        stock_codes: 종목 코드 리스트. 예: ['005930', '360200', '367380']
    """
    return kiwoom.get_multiple_stock_prices(stock_codes)


@mcp.tool()
def get_account_balance() -> dict:
    """내 키움 계좌의 보유종목, 평가금액, 평가손익, 수익률을 조회합니다.
    (계좌평가잔고내역 kt00018)

    Returns:
        총매입/총평가/총손익/총수익률 + 보유종목별 상세(수량·매입가·현재가·평가손익·수익률)
    """
    return kiwoom.get_account_balance()


@mcp.tool()
def get_deposit() -> dict:
    """내 키움 계좌의 예수금 및 주문가능금액을 조회합니다. (kt00001)"""
    return kiwoom.get_deposit()


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "http":
        # Railway는 PORT를 주입(8080)하므로 환경변수를 그대로 사용
        port = int(os.getenv("PORT", "8000"))
        print(f"▶ HTTP 모드 시작 — 포트 {port}")
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        print("▶ stdio 모드로 시작")
        mcp.run()
