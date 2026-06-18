# Lightsail + Tailscale private deploy

이 문서는 BitSwipe를 집 PC가 아니라 AWS Lightsail 서버에서 계속 실행하고,
Tailscale에 로그인한 내 기기에서만 접속하도록 배포하는 절차입니다.

## 목표 구조

```text
내 휴대폰 Tailscale 앱
  -> Tailscale private network
  -> AWS Lightsail Ubuntu server
  -> BitSwipe FastAPI app :8000
```

Lightsail public firewall에는 `8000`을 열지 않습니다. 앱은 서버 안에서
`0.0.0.0:8000`으로 뜨지만, UFW가 `tailscale0` 인터페이스에서 들어오는
요청만 허용합니다.

## 사용자가 직접 해야 하는 것

1. AWS Lightsail에서 Ubuntu 인스턴스를 하나 만듭니다.
   - Platform: Linux/Unix
   - Blueprint: OS Only > Ubuntu
   - Plan: 1 GB RAM 이상 권장
   - Instance name: `bitswipe`
2. Lightsail Networking 방화벽은 `SSH 22`만 열어둡니다.
   - `8000`, `80`, `443`은 열지 않습니다.
3. 서버에 이 프로젝트를 업로드합니다.
   - Windows에서는 아래 PowerShell 스크립트를 쓰면 됩니다.
4. 서버의 `.env`에 OpenAI/Binance/OWNER_PASSWORD 값을 넣습니다.
5. Tailscale 로그인 URL이 나오면 내 계정으로 승인합니다.
6. 휴대폰에 Tailscale 앱을 설치하고 같은 계정으로 로그인합니다.

## Windows PC에서 업로드

Lightsail에서 SSH key 파일(`.pem`)을 받은 뒤 PowerShell에서:

```powershell
.\deploy\lightsail\package_for_lightsail.ps1
.\deploy\lightsail\upload_to_lightsail.ps1 -ServerIp "LIGHTSAIL_PUBLIC_IP" -KeyPath "C:\path\LightsailDefaultKey.pem"
```

업로드가 끝나면 SSH 접속:

```powershell
ssh -i "C:\path\LightsailDefaultKey.pem" ubuntu@LIGHTSAIL_PUBLIC_IP
```

## 서버에서 최초 설치

```bash
cd /opt/bitswipe
cp .env.production.example .env
nano .env
```

`.env`에 최소한 아래 값을 넣습니다.

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
OWNER_PASSWORD=change-this-long-password
DEFAULT_SYMBOL=BTCUSDT
```

저장 후 설치:

```bash
chmod +x deploy/lightsail/bootstrap_ubuntu.sh
APP_DIR=/opt/bitswipe SERVICE_NAME=bitswipe APP_PORT=8000 ./deploy/lightsail/bootstrap_ubuntu.sh
```

설치 중 `tailscale up` 로그인 URL이 나오면 브라우저에서 열고 승인합니다.

## 접속 주소

설치가 끝나면 스크립트가 아래처럼 주소를 출력합니다.

```text
http://100.x.y.z:8000
```

휴대폰에서 Tailscale 앱을 켜고 위 주소로 접속합니다.

MagicDNS를 켠 경우에는 Tailscale 관리 화면에서 서버 이름을 확인한 뒤:

```text
http://bitswipe.tailnet-name.ts.net:8000
```

형태로 접속할 수 있습니다.

## 운영 명령

상태 확인:

```bash
sudo systemctl status bitswipe
```

로그 보기:

```bash
sudo journalctl -u bitswipe -f
```

앱 재시작:

```bash
sudo systemctl restart bitswipe
```

코드 업데이트 후 재설치:

```bash
cd /opt/bitswipe
APP_DIR=/opt/bitswipe SERVICE_NAME=bitswipe APP_PORT=8000 ./deploy/lightsail/bootstrap_ubuntu.sh
```

## 보안 체크리스트

- Lightsail public firewall에서 `8000`을 열지 않습니다.
- Binance API key는 가능하면 출금 권한을 끄고 Futures에 필요한 권한만 둡니다.
- `OWNER_PASSWORD`는 길게 설정합니다.
- Tailscale 계정은 휴대폰 본인 계정으로만 로그인합니다.
- 서버 접속 주소는 public IP가 아니라 Tailscale `100.x.y.z` 주소를 씁니다.
