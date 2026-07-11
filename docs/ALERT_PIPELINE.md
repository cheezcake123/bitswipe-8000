# BitSwipe 알림 파이프라인

## 알림 호출 흐름

상세 진입 심사 알림의 실전 호출 순서는 다음과 같다.

~~~text
분석 완료
→ payload 생성
→ Risk Guard 생성
→ evaluate_trade_alert
→ build_trade_alert_message
→ cooldown 검사
→ Telegram 발송
→ 로그 및 상태 기록
~~~

server.py는 분석 payload를 만든 뒤 maybe_send_trade_alert(payload)를 호출한다. 알림 모듈은 주문을 생성하지 않으며, Telegram 알림 성공 여부와 무관하게 거래를 실행하는 코드가 없다.

## 함수 역할

### normalize_trade_alert_payload(payload)

실제 서버 payload의 다음 구조를 방어적으로 읽어 고정된 내부 후보 형식으로 정규화한다.

- 최상위 symbol, pair_label, price, signal, confidence
- analysis_json
- report_sections
- trade_levels
- risk_guard

잘못된 타입, NaN, 무한대, 음수 가격과 음수 리스크 지표는 표시 가능한 값으로 사용하지 않는다. 원본 payload와 내부 enum은 변경하지 않는다.

### evaluate_trade_alert(payload)

파일, 상태, 로그, 환경변수, Telegram에 접근하지 않는 순수 함수다. 실전 발송 가능 여부와 영문 reason 식별자를 반환한다.

~~~python
{
    "eligible": True,
    "reason": "eligible",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "confidence": 82.0,
    "risk_guard_verdict": "PASS",
}
~~~

대표 reason 값:

- no_payload
- risk_guard_not_pass
- signal_not_buy_or_sell
- risk_guard_side_mismatch
- confidence_too_low
- eligible

### calculate_one_r_target(side, entry_price, stop_price)

LONG은 entry + abs(entry - stop), SHORT는 entry - abs(entry - stop)으로 1R 목표가를 계산한다. 필수 값이 없거나 유효하지 않거나 계산 결과가 0 이하이면 None을 반환한다.

### build_trade_alert_message(payload, test_mode=False)

상세 한국어 메시지를 결정적으로 만드는 순수 함수다. 같은 payload와 같은 옵션은 항상 같은 문자열을 만든다.

이 함수는 다음을 하지 않는다.

- Telegram 호출
- 파일 쓰기
- cooldown 조회 또는 변경
- 후보·최종 판정 로그 기록
- 환경변수 변경

LONG과 SHORT를 모두 지원하고, 손절·목표 방향 오류를 메시지에 경고로 표시한다. 설명 필드의 개수와 길이를 제한하고 핵심 가격·리스크·무효화 정보를 우선 보존해 Telegram의 4096자 제한을 지킨다.

### maybe_send_trade_alert(payload, ...)

실전 I/O 조정 함수다.

1. payload 정규화와 평가
2. 메시지 생성
3. 실전 호출이면 cooldown 확인
4. Telegram 전송
5. 성공 시 cooldown 상태 기록
6. 안전한 결과 로그 기록

dry_run=True이면 sender, 상태파일, cooldown, production 로그에 접근하지 않는다. test_mode=True에서 실제 전송하려면 sender를 명시적으로 주입해야 하며, 이 경로도 production cooldown과 production 로그를 읽거나 변경하지 않는다.

## 실전 발송 기준

모든 조건을 충족해야 한다.

- Risk Guard verdict가 PASS
- 신호 방향이 LONG 또는 SHORT
- 신호 방향과 Risk Guard 검증 방향이 일치
- confidence가 75 이상
- 중복 cooldown 통과

기준을 충족해도 알림은 자동 주문이 아니다. 실제 주문 생성 기능은 이 파이프라인에 없다.

production 상태와 로그:

~~~text
data/telegram_alert_state.json
data/telegram_alert_log.jsonl
~~~

기본 cooldown은 3600초이며 TELEGRAM_ALERT_COOLDOWN_SECONDS로 조정할 수 있다.

## 테스트 방법

기본 실행은 모두 dry-run이다.

~~~bash
python scripts/test_detailed_trade_alert.py --side long
python scripts/test_detailed_trade_alert.py --side short
python scripts/test_detailed_trade_alert.py --side both
~~~

각 실행은 가상 LONG 또는 SHORT payload를 만들고 다음을 검증한다.

- Risk Guard PASS와 confidence 75 이상
- 방향 인식
- 손절과 AI 목표 방향
- LONG/SHORT 1R 목표
- 숫자 손익비
- 한국어 제목과 [테스트] 구분
- Telegram 길이 제한
- production 상태·로그 불변

실패한 검증이 있으면 종료코드 1을 반환한다.

표준 라이브러리 자동 테스트:

~~~bash
python -m unittest tests.test_trade_alert
~~~

전체 테스트 체계:

~~~bash
python -m unittest discover -s tests -p 'test_*.py'
~~~

pytest는 production requirements에 추가하지 않는다.

## 실제 Telegram 테스트 방법

실제 테스트 전송은 --send를 명시한 경우에만 수행한다.

~~~bash
python scripts/test_detailed_trade_alert.py --side long --send
python scripts/test_detailed_trade_alert.py --side both --send
~~~

주의:

- --send 없이는 Telegram을 전송하지 않는다.
- 제목에 [테스트]를 표시한다.
- 테스트는 실제 매매 신호가 아니다.
- production cooldown을 읽거나 변경하지 않는다.
- production 후보 로그와 final verdict 로그에 포함하지 않는다.
- 실제 주문을 생성하지 않는다.
- token과 chat ID를 출력하지 않는다.
- 전송 성공 여부만 출력한다.

## 한국어 표시 원칙

- 사용자에게 보이는 고정 라벨과 경고는 한국어로 작성한다.
- LONG, SHORT, PASS 같은 내부 enum과 API·로그 필드명은 영문으로 유지한다.
- Symbol, 가격, 수치 데이터를 문자열 전체 치환으로 변경하지 않는다.
- 분석 모델이 이미 생성한 한국어 문장은 무차별 후처리하지 않는다.
- 테스트 메시지는 [테스트]로 실전 알림과 구분한다.

## 계좌 기반 포지션 계산 조사

현재 server.py에는 Binance futures account snapshot과 account stream을 읽는 기존 경로가 있지만, 분석 payload를 만드는 _build_payload()는 account 데이터를 의도적으로 포함하지 않는다. 따라서 이번 변경에서는 계좌 잔액을 상세 알림에 연결하지 않는다.

향후에는 주문 권한과 분리된 read-only snapshot을 명시적으로 주입하는 방식이 안전하다.

~~~python
max_loss_usdt = account_balance * account_risk_percent / 100
position_notional = max_loss_usdt / stop_distance_fraction
required_margin = position_notional / leverage
~~~

원칙:

- snapshot이 없으면 기존 퍼센트 표시로 fallback
- API 오류가 분석이나 알림을 중단하지 않음
- 인증정보와 잔액 원문을 로그에 남기지 않음
- 실제 주문 생성 금지

## 배포와 재시작

dry-run과 자동 테스트에는 서비스 재시작이 필요 없다. production 서버가 새 Python 코드를 사용하려면 검증과 배포가 끝난 뒤 해당 서버의 운영 절차에 따라 애플리케이션 프로세스를 한 번 재시작해야 한다. Codex 작업 환경에서 systemd 서비스를 직접 재시작하지 않는다.

## rollback

가장 안전한 rollback은 배포한 변경 커밋을 새 revert 커밋으로 되돌리는 것이다.

~~~bash
git log --oneline --max-count=10
git revert <변경-커밋>
~~~

아직 배포하지 않았다면 작업 브랜치를 병합하지 않는다. 테스트 도구만 제거해야 하는 경우에도 이력 보존을 위해 파일을 직접 삭제해 force push하지 말고 해당 추가 커밋을 revert한다.

rollback 뒤 production 코드가 바뀌었다면 운영 절차에 따라 애플리케이션 프로세스를 재시작하고, 기본 dry-run과 자동 테스트를 다시 실행한다.
