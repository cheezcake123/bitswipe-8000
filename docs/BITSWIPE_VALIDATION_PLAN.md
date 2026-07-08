
# BitSwipe Validation Plan



BitSwipe의 목표는 진입 신호를 많이 만드는 것이 아니라, 위험한 진입을 차단하고 검증 가능한 후보만 남기는 것이다.



## Phase 1 목표



1. 모든 스캔 후보를 구조화된 JSONL 로그로 저장한다.

2. A/B+/B/C/BLOCK/WAIT 판단을 전부 기록한다.

3. entry, stop, target, RR, direction, decision, reason을 기록한다.

4. LLM 호출 여부와 알림 발송 여부를 기록한다.

5. 이후 별도 스크립트로 등급별 결과를 검증할 수 있게 만든다.



## 오늘 구현 범위



- 실시간 매매 판단 로직은 변경하지 않는다.

- 기존 scanner/analyzer 흐름을 최대한 유지한다.

- 후보가 생성될 때 candidate log를 남긴다.

- report script로 로그 요약만 출력한다.



## 나중에 추가할 것



- target-before-stop 백테스트

- WAIT 상태 추적

- BTC market regime filter

- funding rate / open interest

- portfolio exposure check

- LLM JSON output

