# Cài đặt Paradosp SP4000 trên Orange Pi Zero 3 và đẩy dữ liệu SafeLink

Tài liệu này mô tả toàn bộ quy trình từ khi có hai file `paradosp` và
`setup-orange-pi.sh` đến khi Orange Pi đọc được Paradox SP4000, gửi trạng thái
zone lên SafeLink và vận hành ổn định. Cấu hình đã kiểm tra trên Orange Pi Zero
3 H616 với UART5 xuất hiện tại `/dev/ttyS1`.

Luồng tổng thể:

```text
SP4000
  │ Serial TTL 9600 baud
  ▼
Orange Pi UART5 (/dev/ttyS1)
  │
  ▼
PAI đọc frame → parse → cập nhật bộ nhớ trạng thái
  │
  ├─ heartbeat: toàn bộ zone, gửi ngay lần đầu và mỗi 60 giây
  └─ immediately: một thay đổi/sự kiện, đưa vào queue và gửi gần như tức thời
                         │
                         ▼
POST https://safelinksystem.app/core/webhook/ultrasync/crawl
```

## 1. Giá trị vận hành mặc định

| Thành phần | Giá trị |
|---|---|
| Board | Orange Pi Zero 3, SoC H616/H618 |
| UART trên header | UART5 |
| Controller Linux | `5001400.serial` |
| Device đã kiểm tra | `/dev/ttyS1` |
| Baud rate SP4000 | `9600` |
| Serial encryption | `False` |
| PC Communication Password mặc định | `0000` |
| API nội bộ | `0.0.0.0:8082` |
| URL nhận dữ liệu | `https://safelinksystem.app/core/webhook/ultrasync/crawl` |
| Chu kỳ heartbeat | `60` giây |
| Chu kỳ đọc trạng thái mặc định | khoảng `10` giây |
| Queue live event | `256` event |
| HTTP timeout | connect `5` giây, read `10` giây |
| HTTP retry | 1 lần gửi đầu + tối đa 3 lần retry |
| Log hiện tại | `/var/log/paradosp/paradosp.log` |
| Lưu log | xoay theo ngày, gzip, giữ 30 file cũ |

Hai giá trị `type` chính thức gửi SafeLink là `heartbeat` và `immediately`.

## 2. Chuẩn bị phần cứng và phần mềm

Cần có:

- Orange Pi Zero 3 chạy Armbian ARM64.
- Binary ARM64 `paradosp`.
- Script `setup-orange-pi.sh` từ `deploy/orange-pi`.
- PC Communication Password của SP4000 gồm đúng 4 ký tự hexadecimal.
- Mạch chuyển mức hoặc cách ly phù hợp giữa TTL 5 V và GPIO 3,3 V.
- Dây TX, RX và GND.

Không được:

- Nối TX 5 V của SP4000 trực tiếp vào RX 3,3 V của Orange Pi.
- Nối cổng RS232 mức điện áp âm/dương trực tiếp vào GPIO.
- Nối `AUX+` 12 V của tủ vào Orange Pi.
- Để BabyWare, WinLoad, IP150 hoặc một thiết bị Serial khác giữ cùng phiên giao
  tiếp khi PAI đăng nhập panel.

## 3. Bật UART5 trên Armbian

Sao lưu cấu hình boot:

```bash
sudo cp -a /boot/armbianEnv.txt /boot/armbianEnv.txt.bak-uart5
sudo nano /boot/armbianEnv.txt
```

Đảm bảo có:

```text
console=none
overlay_prefix=sun50i-h616
overlays=uart5
```

Nếu đã có overlay khác, giữ lại và thêm `uart5`, phân cách bằng dấu cách:

```text
overlays=i2c3 uart5
```

Không dùng:

```text
overlays=sun50i-h616-uart5
```

`overlay_prefix` đã chứa tiền tố `sun50i-h616`; giá trị trong `overlays` chỉ
cần tên `uart5`.

Tắt login console trên UART0:

```bash
sudo systemctl mask --now serial-getty@ttyS0.service
sudo systemctl daemon-reload
sudo reboot
```

Sau khi máy lên lại, kiểm tra controller:

```bash
sudo dmesg | grep -Ei '5001400|ttyS'

for t in /sys/class/tty/ttyS*; do
  printf '%s -> ' "/dev/${t##*/}"
  readlink -f "$t/device"
done
```

Kết quả đã kiểm tra:

```text
/dev/ttyS0 -> .../5000000.serial/...
/dev/ttyS1 -> .../5001400.serial/...
```

`5000000.serial` là UART0/debug. UART5 cần dùng là controller
`5001400.serial`, xuất hiện tại `/dev/ttyS1` trên image này.

Nếu không có device trỏ tới `5001400.serial`, chưa được tiếp tục cấu hình PAI.
Hãy kiểm tra lại overlay rồi reboot.

## 4. Đấu dây SP4000

```text
SP4000 TX  → chuyển mức 5 V sang 3,3 V → Orange Pi pin 10, UART5 RX
SP4000 RX  ← chuyển mức 3,3 V sang 5 V ← Orange Pi pin 8, UART5 TX
SP4000 GND ──────────────────────────── Orange Pi pin 6, GND
```

Trước khi chạy phần mềm, xác nhận:

- TX và RX đã đấu chéo.
- Hai thiết bị có GND chung theo thiết kế mạch chuyển mức/cách ly.
- Điện áp vào chân RX Orange Pi không vượt 3,3 V.
- Không có dây 12 V nối nhầm vào GPIO.

## 5. Cài binary và service

Chép hai file lên Orange Pi:

```bash
scp paradosp setup-orange-pi.sh admini@IP_ORANGE_PI:~/
ssh admini@IP_ORANGE_PI
```

Kiểm tra kiến trúc và binary:

```bash
cd ~
uname -m
chmod +x paradosp
./paradosp --self-test
```

Kết quả yêu cầu:

```text
aarch64
paradosp self-test OK: platform=Linux arch=aarch64 python=...
```

Cài đặt:

```bash
sudo sh setup-orange-pi.sh ./paradosp
```

Script thực hiện:

1. Cài binary vào `/opt/paradosp/paradosp`.
2. Tạo `/etc/paradosp/paradosp.conf` nếu chưa có.
3. Cấu hình sẵn `/dev/ttyS1`, 9600 baud, không mã hóa, password `0000`.
4. Tạo state tại `/var/lib/paradosp`.
5. Tạo log tại `/var/log/paradosp`.
6. Tạo, enable và khởi động `paradosp.service`.
7. Khi nâng cấp, bổ sung các khóa log còn thiếu nhưng không ghi đè giá trị vận
   hành đã có.

Kiểm tra:

```bash
sudo systemctl status paradosp --no-pager
```

## 6. Cấu hình Serial không cần Postman

Lần cài mới đã có cấu hình:

```python
CONNECTION_TYPE = "Serial"
SERIAL_PORT = "/dev/ttyS1"
SERIAL_BAUD = 9600
SERIAL_ENCRYPTED = False
PASSWORD = "0000"
```

Nếu PC Communication Password thật của panel khác `0000`:

```bash
sudo nano /etc/paradosp/paradosp.conf
sudo systemctl restart paradosp
```

Chỉ sửa:

```python
PASSWORD = "MÃ_PC_4_KÝ_TỰ_HEX"
```

Đây là PC Communication Password trong BabyWare, không phải installer code,
master code hoặc mã user trên keypad.

### Thứ tự ưu tiên cấu hình

Nếu trước đây đã dùng API/Postman, file sau sẽ ghi đè các giá trị Serial trong
`paradosp.conf`:

```text
/var/lib/paradosp/.pai/safelink-serial.json
```

Xem cấu hình runtime:

```bash
curl http://127.0.0.1:8082/api/v1/config/serial
```

Nếu kết quả khác `/etc/paradosp/paradosp.conf`, nguyên nhân thường là file JSON
runtime nói trên. Không xóa file khi chưa xác nhận password và port cần dùng.

## 7. Xác nhận kết nối panel

Kiểm tra process đang giữ UART:

```bash
sudo fuser -v /dev/ttyS1
```

Khi service chạy, process `paradosp` hoặc `python` giữ UART là bình thường. Khi
service đã dừng, device phải không còn process giữ:

```bash
sudo systemctl stop paradosp
sudo fuser -v /dev/ttyS1
sudo stty -F /dev/ttyS1 9600 cs8 -cstopb -parenb
sudo systemctl start paradosp
```

Theo dõi log:

```bash
sudo journalctl -u paradosp -f
```

Luồng thành công:

```text
Connection: Serial(/dev/ttyS1@9600)
Serial port open
Connecting to Panel
Connected via Serial(/dev/ttyS1@9600) to SP4000 ...
Authentication Success
Loading data from panel memory
Running
```

Kiểm tra dữ liệu:

```bash
curl http://127.0.0.1:8082/api/v1/health
curl http://127.0.0.1:8082/api/v1/panel
curl http://127.0.0.1:8082/api/v1/zones
```

Điều kiện đạt:

- `run_state` là `RUN`.
- `panel_detected` là `true`.
- `zones_available` là `true`.
- `/panel` có thông tin panel và dữ liệu storage.
- `/zones` trả map zone thật.

## 8. Contract đẩy dữ liệu SafeLink

Mọi request outbound dùng:

```text
POST https://safelinksystem.app/core/webhook/ultrasync/crawl
Content-Type: application/json
```

Payload ngoài cùng luôn có hai field:

```json
{
  "type": "heartbeat hoặc immediately",
  "data": {}
}
```

Code không đổi tên field, không flatten và không thêm field vào bên trong
`data`. Các object `datetime`, `date` hoặc `time`, kể cả nằm lồng trong
`additional_data`, được encode thành chuỗi ISO-8601.

### 8.1 `heartbeat` là snapshot toàn bộ zone

Cơ chế:

```text
PAI nhận full status đầu tiên
  → lưu snapshot zone mới nhất vào RAM
  → đánh thức SafeLinkPush và gửi ngay một heartbeat
  → sau đó cứ 60 giây lấy snapshot mới nhất và gửi lại
```

Heartbeat không xếp hàng nhiều snapshot lịch sử. Đến thời điểm gửi, worker lấy
map zone mới nhất đang có trong RAM.

Ví dụ:

```json
{
  "type": "heartbeat",
  "data": {
    "1": {
      "id": 1,
      "key": "Front_Door",
      "label": "Front Door",
      "definition": "delay_1",
      "partition": 1,
      "open": false,
      "alarm": false,
      "tamper": false,
      "bypassed": false
    },
    "2": {
      "id": 2,
      "key": "Back_Door",
      "label": "Back Door",
      "definition": "instant",
      "partition": 1,
      "open": true,
      "alarm": false,
      "tamper": false,
      "bypassed": false
    }
  }
}
```

Các field bên trong mỗi zone phụ thuộc model, cấu hình panel và dữ liệu PAI đã
đọc được. Những field thường gặp:

| Field | Ý nghĩa |
|---|---|
| `id` | Số zone trên panel |
| `key` | Khóa nội bộ của zone |
| `label` | Tên zone đọc từ panel |
| `definition` | Loại zone như delay, instant, fire |
| `partition` | Partition chứa zone |
| `open` | `true` khi zone đang mở/active |
| `alarm` | Zone đang trong trạng thái alarm |
| `tamper` | Trạng thái tamper |
| `bypassed` | Zone đang bị bypass |

Map `data` được giữ nguyên; key số của Python như `1` trở thành chuỗi `"1"`
theo chuẩn JSON.

Mục đích heartbeat:

- Đồng bộ toàn bộ trạng thái zone lên backend.
- Phục hồi trạng thái mới nhất sau khi mạng gián đoạn.
- Đối soát nếu một live event bị mất do process chết hoặc queue đầy.
- Không thay thế lịch sử event; heartbeat chỉ phản ánh trạng thái hiện tại.

### 8.2 `immediately` là thay đổi hoặc sự kiện tức thời

`immediately` được tạo từ hai nguồn:

1. Panel gửi live event trực tiếp, ví dụ arm, disarm, alarm hoặc trouble.
2. PAI đọc status và phát hiện một property có giá trị mới khác giá trị cũ.

Luồng xử lý:

```text
Nhận frame Serial
  → parse message
  → cập nhật MemoryStorage
  → so sánh giá trị mới và cũ
     ├─ không đổi: không tạo ChangeEvent
     └─ có đổi: phát event nội bộ
  → SafeLink lọc telemetry không cần thiết
  → put_nowait vào queue
  → SafeLinkPush gửi HTTP
```

Ví dụ zone mở:

```json
{
  "type": "immediately",
  "data": {
    "additional_data": {},
    "change": {"open": true},
    "key": "zone,Front_Door,open=True",
    "label": "Front_Door",
    "level": "DEBUG",
    "message": "Zone Front_Door open",
    "partition": "",
    "property": "open",
    "tags": [],
    "timestamp": 1792176930,
    "type": "zone",
    "value": true
  }
}
```

Ý nghĩa các field event thường gặp:

| Field | Ý nghĩa |
|---|---|
| `timestamp` | Unix timestamp hoặc ISO-8601 tùy nguồn event |
| `type` | Nhóm như `zone`, `partition`, `system`, `module` |
| `id` | ID phần tử nếu event gốc cung cấp |
| `label` | Tên hoặc khóa đối tượng |
| `partition` | Partition liên quan; có thể rỗng với system event |
| `property` | Property vừa đổi như `open`, `alarm`, `arm`, `tamper` |
| `value` | Giá trị mới của property |
| `change` | Object chứa property và giá trị mới |
| `level` | `DEBUG`, `INFO`, `WARN`, `ERROR`, `CRITICAL` |
| `message` | Mô tả do property/event map của PAI tạo |
| `tags` | Nhãn phân loại phục vụ lọc/xử lý |
| `key` | Khóa mô tả event do PAI tạo |
| `additional_data` | Dữ liệu bổ sung, có thể chứa object lồng nhau |

Ví dụ arm partition:

```json
{
  "type": "immediately",
  "data": {
    "type": "partition",
    "partition": 1,
    "label": "Main House",
    "property": "arm",
    "value": true,
    "change": {"arm": true},
    "level": "INFO",
    "tags": ["arm"]
  }
}
```

Ví dụ alarm zone:

```json
{
  "type": "immediately",
  "data": {
    "type": "zone",
    "label": "Front_Door",
    "property": "alarm",
    "value": true,
    "change": {"alarm": true},
    "level": "CRITICAL",
    "tags": ["alarm"]
  }
}
```

Ví dụ thời gian lồng nhau đã được chuẩn hóa:

```json
{
  "type": "immediately",
  "data": {
    "additional_data": {
      "occurred_at": "2026-10-17T08:30:45+00:00"
    }
  }
}
```

### 8.3 Khi nào `immediately` được push

- Zone mở hoặc đóng.
- Zone alarm hoặc alarm restore.
- Tamper thay đổi.
- Zone bị bypass hoặc bỏ bypass.
- Partition arm, arm stay, arm sleep hoặc disarm.
- Entry delay hoặc exit delay thay đổi.
- Bell, fire, panic hoặc trouble thay đổi.
- Mất AC, pin yếu, lỗi communication hoặc trạng thái nguồn quan trọng.
- Live event thật được panel phát qua Serial.

Quy tắc chống dữ liệu rác:

- Property không đổi thì không tạo lại ChangeEvent khi
  `PUSH_UPDATE_WITHOUT_CHANGE=False`.
- Property được khởi tạo lần đầu không phát hàng loạt `immediately`.
- Telemetry điện áp mức `DEBUG` có property `battery`, `dc` hoặc `vdc` và tag
  `voltage` bị lọc.
- Event battery/power dạng `trouble` hoặc mức `CRITICAL` không bị lọc.
- Live event thật do panel chủ động phát vẫn được chuyển tiếp; nó không phải
  một lần poll trùng giá trị.

Ví dụ bị lọc, không gửi SafeLink:

```json
{
  "type": "immediately",
  "data": {
    "type": "system",
    "label": "power",
    "level": "DEBUG",
    "property": "battery",
    "value": 13.8,
    "tags": ["voltage", "battery"]
  }
}
```

Ví dụ vẫn phải gửi:

```json
{
  "type": "immediately",
  "data": {
    "type": "system",
    "level": "CRITICAL",
    "property": "battery_failure_trouble",
    "value": true,
    "tags": ["battery", "trouble"]
  }
}
```

## 9. Queue, retry và giới hạn mất dữ liệu

HTTP outbound chạy trong thread `SafeLinkPush`, tách khỏi asyncio/Serial loop.
Callback nhận event chỉ snapshot dữ liệu và gọi `put_nowait`; nó không chờ HTTP.

| Cơ chế | Hành vi |
|---|---|
| Queue | Tối đa 256 live event trong RAM |
| Queue còn chỗ | Event được thêm và worker xử lý theo thứ tự |
| Queue đầy | Bỏ event mới, tăng `dropped_events`, ghi log lỗi |
| HTTP thành công | Cập nhật thời điểm và counter tương ứng |
| HTTP thất bại | Tổng tối đa 4 lần thử, backoff 1, 2, 4 giây |
| Mạng chậm | Worker có thể chậm, nhưng Serial loop vẫn tiếp tục |
| Process restart/mất điện | Event còn trong RAM queue bị mất |
| Heartbeat | Không dùng hàng đợi lịch sử; luôn lấy snapshot zone mới nhất |

Nếu mạng hỏng lâu, worker bận retry trong khi event mới tiếp tục vào queue. Khi
queue đạt 256 phần tử, event mới bị bỏ. Vì vậy phải giám sát cả
`queued_events` và `dropped_events`.

Heartbeat giúp backend lấy lại trạng thái hiện tại nhưng không thể phục hồi đầy
đủ thứ tự lịch sử event đã mất. Nếu lịch sử tuyệt đối không được phép mất, cần
bổ sung durable disk spool hoặc message broker; phiên bản hiện tại chưa có.

## 10. API vận hành trên Orange Pi

| Method | Endpoint | Nội dung |
|---|---|---|
| `GET` | `/api/v1/health` | Tình trạng panel, zone, push, queue và counter |
| `GET` | `/api/v1/panel` | Panel, run state, cấu hình kết nối và toàn bộ storage |
| `GET` | `/api/v1/zones` | Map zone mới nhất, cũng là `data` heartbeat |
| `GET` | `/api/v1/config/serial` | Cấu hình Serial runtime |
| `PUT/POST` | `/api/v1/config/serial` | Lưu cấu hình Serial và yêu cầu reconnect |

Health mẫu:

```json
{
  "status": "ok",
  "run_state": "RUN",
  "panel_detected": true,
  "zones_available": true,
  "last_status_at": 1792176995.12,
  "last_push_at": 1792177000.45,
  "last_heartbeat_at": 1792177000.45,
  "last_event_push_at": 1792176990.20,
  "last_push_error": null,
  "queued_events": 0,
  "dropped_events": 0,
  "filtered_events": 15,
  "heartbeat_push_count": 10,
  "event_push_count": 3
}
```

| Field | Ý nghĩa | Trạng thái tốt |
|---|---|---|
| `last_status_at` | Lần cuối PAI cập nhật status panel | Liên tục được cập nhật |
| `last_push_at` | Lần gửi bất kỳ thành công gần nhất | Có timestamp mới |
| `last_heartbeat_at` | Lần heartbeat thành công gần nhất | Thay đổi khoảng mỗi 60 giây |
| `last_event_push_at` | Lần `immediately` thành công gần nhất | Thay đổi khi có event |
| `last_push_error` | Lỗi encode hoặc HTTP cuối cùng | `null` sau lần gửi thành công |
| `queued_events` | Số live event đang chờ | Thường gần `0` |
| `dropped_events` | Event bị bỏ do queue đầy | Phải bằng `0` |
| `filtered_events` | Telemetry DEBUG đã chủ động lọc | Có thể tăng, không phải lỗi |
| `heartbeat_push_count` | Tổng heartbeat thành công từ khi process chạy | Tăng mỗi phút |
| `event_push_count` | Tổng live event thành công từ khi process chạy | Tăng khi có sự kiện |

Các counter reset về `0` khi process restart.

## 11. Log theo ngày

```text
/var/log/paradosp/paradosp.log
/var/log/paradosp/paradosp.log.2026-10-16.gz
```

Xem log:

```bash
sudo tail -f /var/log/paradosp/paradosp.log
sudo journalctl -u paradosp -f
sudo zless /var/log/paradosp/paradosp.log.2026-10-16.gz
```

Heartbeat thành công ghi:

```text
SafeLink heartbeat delivered: N zone(s)
```

Phiên bản hiện tại encode `datetime`, `date` và `time` thành ISO-8601, kể cả
khi nằm lồng trong `additional_data`.

### Bật debug Serial tạm thời

Trong `/etc/paradosp/paradosp.conf`:

```python
import logging

LOGGING_LEVEL_CONSOLE = logging.DEBUG
LOGGING_DUMP_PACKETS = True
LOGGING_DUMP_MESSAGES = True
```

Sau khi kiểm tra phải trả về `INFO` và tắt hai cờ dump vì packet debug có thể
chứa dữ liệu nhạy cảm.

## 12. Backend SafeLink hiện tại

Endpoint `/core/webhook/ultrasync/crawl` hiện nhận object JSON, chỉ chấp nhận
hai giá trị `type` là `heartbeat` và `immediately`, sau đó lưu đúng `type` cùng
raw payload vào bảng `nxg8_crawl`. Giá trị thiếu hoặc kiểu cũ như `typeevent`
và `heatbeath` bị trả HTTP 400. Heartbeat toàn bộ zone có thể lớn hơn giới hạn
`VARCHAR(255)`, vì vậy backend đã có migration
`V20__expand_nxg8_crawl_payload.sql` để đổi `data` sang `TEXT`.

SQL tương ứng:

```sql
ALTER TABLE IF EXISTS nxg8_crawl
    ALTER COLUMN data TYPE TEXT;
```

`spring.flyway.enabled` hiện đang là `false`; migration phải được chạy bằng quy
trình triển khai database của SafeLink hoặc bật Flyway sau khi đã rà soát toàn
bộ lịch sử migration. Chỉ deploy file Java mà chưa đổi cột database sẽ không
xử lý được HTTP 500 do payload quá dài.

Endpoint đã kiểm tra và lưu riêng tên loại `heartbeat`/`immediately`, nhưng chưa
upsert trạng thái zone và chưa có idempotency.

Hệ quả:

- Một heartbeat mỗi phút tạo khoảng 1.440 row/ngày cho mỗi Orange Pi.
- Các `immediately` tạo thêm row theo số sự kiện thực tế.
- HTTP 2xx chỉ xác nhận backend nhận/lưu request, chưa chứng minh trạng thái
  zone đã được xử lý cho UI.
- Backend cần retention hoặc bảng latest snapshot nếu vận hành lâu dài.
- `/crawl` hiện không yêu cầu token từ client Paradosp; cần giới hạn mạng hoặc
  bổ sung xác thực trước khi mở rộng production.

Backend nên xử lý:

```text
heartbeat → upsert snapshot mới nhất của tất cả zone
immediately → lưu lịch sử và xử lý cảnh báo/thay đổi tức thời
```

### Thứ tự triển khai bắt buộc

1. Chạy migration `V20__expand_nxg8_crawl_payload.sql` trên database SafeLink.
2. Deploy backend SafeLink có hỗ trợ `heartbeat` và `immediately`.
3. Deploy binary Paradosp mới lên Orange Pi rồi restart `paradosp.service`.
4. Kiểm tra HTTP 2xx, `/api/v1/health` và dữ liệu thực tế trong `nxg8_crawl`.

Nên thực hiện bước 2 và 3 gần nhau: backend mới cố ý từ chối type cũ, còn
backend cũ có thể chưa nhận đúng hợp đồng mới. Không coi việc copy migration
vào source là đã thay đổi database vì Flyway đang tắt.

## 13. Kiểm thử end to end

### Heartbeat

```bash
watch -n 5 'curl -s http://127.0.0.1:8082/api/v1/health'
```

Đạt khi:

- `heartbeat_push_count` tăng.
- `last_heartbeat_at` thay đổi khoảng mỗi 60 giây.
- `last_push_error` là `null`.
- Log có `SafeLink heartbeat delivered: N zone(s)`.
- `N` khớp số zone trong `/api/v1/zones`.

### Live event

Với từng zone vật lý:

1. Ghi lại zone ID, label và partition.
2. Mở cửa hoặc kích PIR.
3. Xác nhận đúng zone chuyển `open: false → true`.
4. Đóng cửa hoặc để PIR trở lại bình thường.
5. Xác nhận `open: true → false`.
6. Kiểm tra `event_push_count` tăng.
7. Xác nhận backend nhận hai `immediately` đúng zone, đúng chiều thay đổi.

Kiểm tra riêng tamper, bypass, arm/disarm và alarm. Chỉ test alarm theo quy
trình an toàn, có người phụ trách và đã cô lập đường báo trung tâm nếu cần.

### Telemetry nguồn

- `battery`, `dc`, `vdc` mức `DEBUG` không xuất hiện tại backend.
- `filtered_events` tăng khi các mẫu điện áp bị lọc.
- Event mất nguồn hoặc pin yếu mức `CRITICAL` vẫn tới backend.

### Mất mạng

1. Chặn tạm đường tới backend trong môi trường kiểm thử được phép.
2. Tạo một số event có kiểm soát.
3. Xác nhận Serial và `/api/v1/zones` vẫn cập nhật.
4. Theo dõi `queued_events` tăng nhưng không vượt 256.
5. Khôi phục mạng và xác nhận worker tiếp tục gửi.
6. `dropped_events` phải bằng `0`.

## 14. Chẩn đoán nhanh

| Dấu hiệu | Nguyên nhân gần nhất | Xử lý |
|---|---|---|
| Chỉ thấy `PAI -> SER` | Sai UART, dây, mức điện áp hoặc panel bị phiên khác giữ | Kiểm tra `5001400.serial`, TX/RX/GND, level shifter và BabyWare/WinLoad |
| Kết nối `/dev/ttyS0` thất bại | Đang dùng UART0/debug | Dùng device trỏ tới `5001400.serial`, trên máy này là `/dev/ttyS1` |
| Không có `5001400.serial` | Overlay UART5 chưa nạp | Đặt `overlays=uart5`, reboot |
| `Authentication Failed` | Sai PC Password | Kiểm tra BabyWare và file JSON runtime |
| Có zone nhưng không heartbeat | Worker/backend lỗi | Xem `last_heartbeat_at`, `last_push_error`, journal |
| Nhiều event điện áp DEBUG | Binary cũ | Build và cài binary mới |
| `datetime is not JSON serializable` | Binary cũ chưa có ISO encoder | Build và cài binary mới |
| `queued_events` tăng liên tục | Backend chậm hoặc mất mạng | Kiểm tra URL, DNS, TLS, latency và response |
| `dropped_events > 0` | Queue 256 đã đầy | Khắc phục backend/mạng; heartbeat không phục hồi lịch sử |

## 15. Cập nhật binary

```bash
sudo sh setup-orange-pi.sh ./paradosp
sudo systemctl status paradosp --no-pager
sudo journalctl -u paradosp -n 100 --no-pager
curl http://127.0.0.1:8082/api/v1/health
curl http://127.0.0.1:8082/api/v1/zones
```

Script giữ cấu hình hiện có, bổ sung khóa log còn thiếu, thay binary, chạy
self-test và restart service.

## 16. Checklist bàn giao

- [ ] `uname -m` trả `aarch64`.
- [ ] UART5 xuất hiện dưới controller `5001400.serial`.
- [ ] PAI dùng `/dev/ttyS1@9600` trên máy đã kiểm tra.
- [ ] TX/RX đấu chéo qua mạch chuyển mức và có GND chung.
- [ ] Log có `Authentication Success` và `Running`.
- [ ] Health có `run_state=RUN` và `zones_available=true`.
- [ ] `/api/v1/zones` trả đúng zone thật.
- [ ] `heartbeat_push_count` tăng mỗi phút.
- [ ] `last_heartbeat_at` liên tục được cập nhật.
- [ ] `immediately` đúng zone khi mở/đóng thực tế.
- [ ] `event_push_count` tăng khi có sự kiện.
- [ ] Telemetry điện áp DEBUG bị lọc nhưng trouble nguồn vẫn được gửi.
- [ ] `last_push_error=null`.
- [ ] `dropped_events=0`.
- [ ] Log ngày cũ được nén `.gz`.
- [ ] Đã tắt packet debug sau nghiệm thu.
- [ ] Port `8082` chỉ truy cập từ mạng quản trị tin cậy.
- [ ] Backend có retention và xử lý riêng `heartbeat`/`immediately`.
