"""
키움증권 REST API 클라이언트 (완성본)
- 토큰 자동 발급 및 캐싱 (만료 타임존 보정 + 8005 자동 재발급)
- 국내 주식/ETF 시세 조회 (ka10001)
- 계좌평가잔고내역 조회 = 보유종목·평가금액·수익률 (kt00018)

[필드명 검증 완료]
  시세(ka10001)·계좌(kt00018) 응답 필드는 실제 응답으로 대조하여 확정함.
  (raw=True 옵션은 디버깅용으로 유지)

[KIS → 키움 핵심 차이]
  - 요청 방식: KIS는 GET+params, 키움은 POST+JSON body
  - TR 지정: KIS는 헤더 tr_id, 키움은 헤더 api-id
  - 인증 헤더: 키움은 appsecret 헤더 불필요 (Bearer 토큰만)
  - 토큰 응답: KIS access_token/expires_in(초), 키움 token/expires_dt(일시, KST)
  - 성공 판정: KIS rt_cd=="0", 키움 return_code==0

[토큰 만료 처리 — 중요]
  키움 expires_dt는 KST 'YYYYMMDDHHMMSS'. naive datetime으로 .timestamp()를 부르면
  서버 로컬타임(UTC)으로 해석돼 만료시각이 9시간 뒤로 밀려, 죽은 토큰을 계속 재사용하다
  8005가 발생함 → tzinfo=KST 명시로 보정. 추가로 8005 응답 시 강제 재발급 후 1회 재시도.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# 키움 expires_dt는 KST 기준
KST = timezone(timedelta(hours=9))


def _to_int(value) -> int:
    """키움 응답의 숫자 문자열을 정수로 변환.
    부호('+74000','-700')·콤마·공백이 붙은 문자열을 줄 수 있어 정리."""
    if value is None or value == "":
        return 0
    try:
        return int(float(str(value).replace("+", "").replace(",", "").strip()))
    except ValueError:
        return 0


def _to_float(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace("+", "").replace(",", "").strip())
    except ValueError:
        return 0.0


class KiwoomClient:
    """키움증권 REST API 클라이언트"""

    def __init__(self, is_mock: Optional[bool] = None):
        self.app_key = os.getenv("KIWOOM_APP_KEY")
        self.app_secret = os.getenv("KIWOOM_APP_SECRET")

        if is_mock is None:
            is_mock = os.getenv("KIWOOM_IS_MOCK", "false").lower() == "true"
        self.is_mock = is_mock

        self.base_url = (
            "https://mockapi.kiwoom.com" if is_mock else "https://api.kiwoom.com"
        )

        if not all([self.app_key, self.app_secret]):
            raise ValueError(
                "환경변수 누락: KIWOOM_APP_KEY, KIWOOM_APP_SECRET 확인"
            )

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    # ------------------------------------------------------------------ #
    # 토큰
    # ------------------------------------------------------------------ #
    def _get_access_token(self, force: bool = False) -> str:
        """Access Token 발급 (캐싱, 만료 10분 전 자동 재발급).
        force=True면 캐시를 무시하고 무조건 새로 발급."""
        if (
            not force
            and self._access_token
            and time.time() < self._token_expires_at - 600
        ):
            return self._access_token

        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,   # 키움은 'secretkey' (KIS는 'appsecret')
        }

        response = requests.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

        if str(data.get("return_code", "0")) not in ("0", ""):
            raise RuntimeError(
                f"키움 토큰 발급 오류 [{data.get('return_code')}]: {data.get('return_msg')}"
            )

        self._access_token = data.get("token") or data.get("access_token")

        # 키움 expires_dt는 'YYYYMMDDHHMMSS' (KST). naive로 파싱하면 서버 로컬(UTC)로
        # 해석돼 만료시각이 9시간 미뤄지는 버그 → tzinfo=KST를 명시해 정확히 환산.
        expires_dt = data.get("expires_dt")
        try:
            self._token_expires_at = (
                datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
                .replace(tzinfo=KST)
                .timestamp()
            )
        except (TypeError, ValueError):
            # expires_dt 누락/형식 변경 시 보수적으로 12시간만 캐싱
            self._token_expires_at = time.time() + 12 * 3600

        # Railway 로그에서 재발급 여부를 추적할 수 있게 기록
        try:
            _exp = datetime.fromtimestamp(self._token_expires_at, KST).strftime(
                "%Y-%m-%d %H:%M:%S KST"
            )
        except (OSError, OverflowError, ValueError):
            _exp = str(self._token_expires_at)
        print(f"[kiwoom] access token issued (expires_at={_exp})", flush=True)

        return self._access_token

    # ------------------------------------------------------------------ #
    # 공통 요청 헬퍼
    # ------------------------------------------------------------------ #
    def _request(
        self,
        api_id: str,
        endpoint: str,
        body: Optional[dict] = None,
        cont_yn: str = "N",
        next_key: str = "",
        _retry_on_auth: bool = True,
    ) -> dict:
        """키움 REST API 공통 POST 요청.
        토큰 무효(8005) 응답 시 토큰을 강제 재발급하고 1회 자동 재시도한다."""
        token = self._get_access_token()
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }

        response = requests.post(url, headers=headers, json=body or {})
        response.raise_for_status()
        data = response.json()

        if str(data.get("return_code", "0")) not in ("0", ""):
            return_msg = str(data.get("return_msg", ""))
            # 토큰 무효(8005): 캐시 폐기 후 강제 재발급하여 1회만 재시도
            if _retry_on_auth and "8005" in return_msg:
                print(
                    "[kiwoom] token invalid (8005) → force refresh & retry",
                    flush=True,
                )
                self._get_access_token(force=True)
                return self._request(
                    api_id,
                    endpoint,
                    body,
                    cont_yn,
                    next_key,
                    _retry_on_auth=False,
                )
            raise RuntimeError(
                f"키움 API 오류 [{api_id}/{data.get('return_code')}]: {data.get('return_msg')}"
            )

        data["_cont_yn"] = response.headers.get("cont-yn", "N")
        data["_next_key"] = response.headers.get("next-key", "")
        return data

    # ------------------------------------------------------------------ #
    # 시세
    # ------------------------------------------------------------------ #
    def get_stock_price(self, stock_code: str, raw: bool = False) -> dict:
        """국내 주식/ETF 현재가 조회 (ka10001 주식기본정보요청)"""
        data = self._request(
            api_id="ka10001",
            endpoint="/api/dostk/stkinfo",
            body={"stk_cd": stock_code},
        )
        if raw:
            return data

        return {
            "market": "KR",
            "stock_code": stock_code,
            "name": data.get("stk_nm", "N/A"),
            "current_price": abs(_to_int(data.get("cur_prc"))),
            "currency": "KRW",
            "change": _to_int(data.get("pred_pre")),       # 전일대비
            "change_rate": _to_float(data.get("flu_rt")),  # 등락율(%)
            "open": abs(_to_int(data.get("open_pric"))),
            "high": abs(_to_int(data.get("high_pric"))),
            "low": abs(_to_int(data.get("low_pric"))),
            "volume": _to_int(data.get("trde_qty")),
            "market_cap": _to_int(data.get("mac")),        # 시가총액
            "per": data.get("per", "N/A"),
            "pbr": data.get("pbr", "N/A"),
            "eps": data.get("eps", "N/A"),
            "roe": data.get("roe", "N/A"),
        }

    def get_multiple_stock_prices(self, stock_codes: list[str]) -> list[dict]:
        """여러 종목 시세 일괄 조회 (TR당 1 req/s 제한 → 순차 호출)"""
        results = []
        for code in stock_codes:
            try:
                results.append(self.get_stock_price(code))
            except Exception as e:
                results.append({"stock_code": code, "error": str(e)})
            time.sleep(1.0)  # 키움 레이트리밋(TR당 1 req/s) 대응
        return results

    # ------------------------------------------------------------------ #
    # 계좌  ★ 전환의 핵심 — 실제 보유종목·평가손익·수익률 (검증 완료)
    # ------------------------------------------------------------------ #
    def get_account_balance(self, raw: bool = False) -> dict:
        """계좌평가잔고내역 조회 (kt00018) = 보유종목 + 평가금액 + 수익률"""
        data = self._request(
            api_id="kt00018",
            endpoint="/api/dostk/acnt",
            body={
                "qry_tp": "1",          # 조회구분 (1:합산)
                "dmst_stex_tp": "KRX",  # 국내거래소구분
            },
        )
        if raw:
            return data

        holdings = []
        for item in data.get("acnt_evlt_remn_indv_tot", []):
            holdings.append({
                "stock_code": item.get("stk_cd", ""),
                "name": item.get("stk_nm", ""),
                "quantity": _to_int(item.get("rmnd_qty")),           # 보유수량
                "sellable_qty": _to_int(item.get("trde_able_qty")),  # 매매가능수량
                "avg_price": _to_int(item.get("pur_pric")),          # 매입가
                "current_price": _to_int(item.get("cur_prc")),       # 현재가
                "purchase_amount": _to_int(item.get("pur_amt")),     # 매입금액
                "eval_amount": _to_int(item.get("evlt_amt")),        # 평가금액
                "profit_loss": _to_int(item.get("evltv_prft")),      # 평가손익
                "profit_rate": _to_float(item.get("prft_rt")),       # 수익률(%)
                "weight": _to_float(item.get("poss_rt")),            # 보유비중(%)
            })

        return {
            "total_purchase": _to_int(data.get("tot_pur_amt")),       # 총매입금액
            "total_eval": _to_int(data.get("tot_evlt_amt")),          # 총평가금액
            "total_profit_loss": _to_int(data.get("tot_evlt_pl")),    # 총평가손익
            "total_profit_rate": _to_float(data.get("tot_prft_rt")),  # 총수익률(%)
            "estimated_deposit_asset": _to_int(data.get("prsm_dpst_aset_amt")),  # 추정예수자산
            "holdings": holdings,
            "holding_count": len(holdings),
        }


# 모듈 단독 실행 시 테스트
if __name__ == "__main__":
    client = KiwoomClient()  # .env의 KIWOOM_IS_MOCK 값에 따름

    print("▶ 토큰 발급 테스트...")
    token = client._get_access_token()
    print(f"✅ 토큰 발급 완료\n")

    print("▶ [시세] 삼성전자(005930)")
    price = client.get_stock_price("005930")
    print(f"✅ {price['name']}: {price['current_price']:,}원 ({price['change_rate']:+.2f}%)\n")

    print("▶ [계좌] 평가잔고내역 ★")
    bal = client.get_account_balance()
    print(f"✅ 총평가 {bal['total_eval']:,}원 / 총손익 {bal['total_profit_loss']:,}원 "
          f"({bal['total_profit_rate']:+.2f}%) / 보유 {bal['holding_count']}종목")
    for h in bal["holdings"]:
        print(f"   - {h['name']}: {h['quantity']}주, "
              f"평가 {h['eval_amount']:,}원 ({h['profit_rate']:+.2f}%)")
