# Hướng dẫn tích hợp Paradox SP4000 → Orange Pi 3 → PAI → SafeLink

## 1. Mục tiêu và kết luận triển khai

Mục tiêu của hệ thống:

1. Orange Pi 3 kết nối trực tiếp với cổng serial của tủ Paradox SP4000.
2. PAI nhận byte từ tủ, tách frame, kiểm tra checksum, nhận diện đúng SP4000 và đăng nhập bằng PC Password.
3. PAI đọc cấu hình zone và tên zone từ EEPROM của tủ.
4. PAI đọc trạng thái hiện tại của toàn bộ zone đang được cấu hình, đồng thời nhận live event để cập nhật nhanh.
5. Cứ 60 giây tạo một snapshot đầy đủ và `POST` lên:

   ```text
   https://safelinksystem.app/core/webhook/ultrasync/crawl
   ```

> Domain đúng là `safelinksystem.app`. Domain `safelinkssytem.app` trong yêu cầu ban đầu bị đảo chữ `system` và hiện không có DNS.

Kiến trúc khuyến nghị:

```mermaid
flowchart LR
    Z["Cảm biến / zone"] --> P["Paradox SP4000"]
    P -->|"Serial TTL 5 V, 9600 bps"| L["Bộ chuyển mức hoặc USB-TTL phù hợp 5 V"]
    L -->|"USB /dev/serial/by-id/..."| O["Orange Pi 3"]
    O --> S["PAI SerialConnectionProtocol"]
    S --> F["SerialFramer: frame 37 byte + checksum"]
    F --> D["Parser Spectra/Magellan"]
    D --> M["EEPROM definitions + labels"]
    D --> R["RAM status blocks + live events"]
    M --> C["Bộ nhớ trạng thái PAI"]
    R --> C
    C --> B["SafeLinkCrawlInterface: snapshot mới nhất"]
    B -->|"Mỗi 60 giây, HTTPS POST"| W["SafeLink /core/webhook/ultrasync/crawl"]
```

Đây là luồng nên dùng cho bản production đầu tiên. Không nên tự viết lại protocol Paradox trên Orange Pi khi PAI đã có parser, retry, event map, status poll và storage tương ứng với SP/MG.

## 2. Phạm vi kiểm tra và mức độ xác minh

Tài liệu này được tổng hợp từ:

- Toàn bộ tài liệu chữ trong thư mục `docs` của repo này: kiến trúc tổng thể, serial SP/MG, PRT3, serial encryption và arming flow.
- Toàn bộ 17 trang nội dung và `_Sidebar` của [PAI Wiki](https://github.com/ParadoxAlarmInterface/pai/wiki), lấy từ Git repository của Wiki ngày 2026-10-15.
- Code thực tế ở nhánh hiện tại, commit `dc9384bdc0c057d649c5dca55157f638b9721949`.
- Code local hiện tại của `safelink-core-service` cho endpoint `/webhook/ultrasync/crawl`.
- Trang sản phẩm và manual chính thức của Paradox cho SP4000.

Mức độ xác minh:

| Nội dung | Đã xác minh |
|---|---|
| Flow PAI, framing, parser, EEPROM, RAM blocks, storage | Đọc trực tiếp code |
| Danh sách tài liệu Wiki và nội dung serial/MQTT/config | Clone và đọc Wiki |
| SP4000 được PAI map vào Spectra/Magellan | Đọc trực tiếp factory và product ID enum |
| URL đúng của SafeLink | DNS hoạt động; `GET` trả `405`, phù hợp endpoint chỉ nhận `POST` |
| Contract hiện tại của `/crawl` | Đọc controller/entity local của SafeLink |
| Test unit local | Chưa chạy được vì môi trường thiếu dependency Python `construct` |
| Kết nối Orange Pi 3 ↔ SP4000 thật | Chưa kiểm tra phần cứng |
| PC Password, firmware, số zone và sơ đồ dây thực tế tại hiện trường | Cần xác nhận trên tủ thật |
| `POST` thật lên production | Không thực hiện để tránh tạo dữ liệu ngoài ý muốn |

## 3. Thuật ngữ cần phân biệt

| Thuật ngữ | Ý nghĩa |
|---|---|
| Zone | Một đầu vào/cảm biến của tủ: cửa từ, PIR, báo khói, tamper... |
| `open` | Vòng dây/đầu vào zone đang bị vi phạm. Không đồng nghĩa chắc chắn đang báo động. |
| `alarm` | Zone đang ở trạng thái alarm theo RAM của tủ. |
| `was_in_alarm` | Zone đã từng alarm và còn trong bộ nhớ alarm. Không phải alarm hiện tại. |
| `tamper` | Trạng thái phá hoại/mở nắp/đứt đường tamper. |
| `bypassed` | Zone bị bỏ qua khi arm. Zone bypass vẫn có thể đang `open`. |
| Partition/area | Nhóm zone được arm/disarm cùng nhau. SP4000 hỗ trợ tối đa 2 partition. |
| EEPROM definition | Cấu hình lâu dài của zone: disabled/delay/instant/fire..., partition, bypassable... |
| RAM status | Trạng thái đang xảy ra tại thời điểm poll. |
| Live event | Sự kiện bất đồng bộ do panel tự đẩy ngay khi có thay đổi. |
| Snapshot | Ảnh chụp logic của toàn bộ zone tại một thời điểm, không phải ảnh camera. |

Điểm quan trọng: `open`, `alarm`, `tamper`, `fire` và `bypassed` là các cờ khác nhau. Không được gom tất cả thành một boolean duy nhất rồi làm mất ý nghĩa.

## 4. Phần cứng SP4000 và giới hạn zone

Theo [trang SP4000 chính thức của Paradox](https://www.paradox.com/Products/CAT_SPECTRA/ControlPanels.asp?CATID=6&PRD=1587&SUBCATID=61):

- SP4000 có 4 zone on-board.
- Có thể dùng ATZ để tăng số đầu vào hữu dụng trên bo.
- Có thể mở rộng tới 32 zone bằng module hardwired/wireless phù hợp.
- Hỗ trợ 2 partition và 32 user code.
- Có cổng serial cho IP150/307USB.

PAI dùng chung parser Spectra/Magellan và giải mã bitmap/status cho zone `1..32`. Tuy nhiên, snapshot production chỉ nên coi các zone đã được panel định nghĩa là zone hoạt động. Zone `definition=disabled` không được tự gán thành `closed`.

### 4.1 Cổng serial của panel

[PAI Wiki - Connection methods](https://github.com/ParadoxAlarmInterface/pai/wiki/Connection-methods#serial-connection) mô tả đầu nối:

```text
┌───────┐
│ Rx   ┌╵
│ Tx   │
│ GND  │
│ AUX+ └╷
└───────┘
```

- `RX/TX`: logic TTL 5 V.
- `GND`: mass chung của tín hiệu.
- `AUX+`: 12 V, không phải chân 5 V.
- Serial SP/MG chạy 9600 baud.
- PAI/pyserial mặc định dùng 8 data bit, no parity, 1 stop bit.

### 4.2 Tuyệt đối không nối trực tiếp vào Orange Pi

GPIO UART của Orange Pi là 3,3 V. Không nối trực tiếp tín hiệu 5 V của SP4000 vào GPIO RX Orange Pi. Không nối `AUX+ 12 V` vào chân 5 V/3,3 V của Orange Pi.

Phương án khuyến nghị theo thứ tự:

1. Paradox 307USB nếu đang có sẵn.
2. USB-TTL có logic 5 V thật và được xác nhận datasheet.
3. USB-TTL 3,3 V kết hợp bộ chuyển mức hai chiều phù hợp hoặc mạch cách ly quang được thiết kế đúng.
4. UART GPIO Orange Pi chỉ dùng khi có mạch chuyển mức/cách ly đúng và đã tắt serial console của Linux.

Không dùng bộ chuyển USB-RS232 điện áp chuẩn DB9 ±12 V trực tiếp với cổng TTL của panel. Tên bán hàng “USB RS232 TTL” thường gây nhầm; phải kiểm tra mức điện áp thực tế của chân TX/RX.

### 4.3 Sơ đồ dây tối thiểu

```text
SP4000 TX  ─────────>  RX của USB-TTL / level shifter
SP4000 RX  <─────────  TX của USB-TTL / level shifter
SP4000 GND ──────────  GND tín hiệu của adapter
SP4000 AUX+            KHÔNG NỐI vào Orange Pi

Orange Pi 3 được cấp nguồn riêng, ổn định.
```

Nếu không handshake được, kiểm tra lại hướng TX/RX theo đúng nhãn trên bo thực tế. Chỉ đổi chéo TX/RX sau khi đã chắc chắn mức điện áp an toàn.

### 4.4 Quy tắc vận hành cổng serial

- Cổng serial phải rảnh. Không để BabyWare, 307USB khác, IP150 hoặc một process khác cùng giữ kết nối.
- Dùng tên thiết bị ổn định `/dev/serial/by-id/...`, không phụ thuộc hoàn toàn vào `/dev/ttyUSB0` vì số thứ tự có thể đổi sau reboot.
- User chạy PAI cần quyền đọc/ghi thiết bị, thường qua group `dialout`.
- Dùng nguồn riêng tốt cho Orange Pi, watchdog và UPS nếu đây là hệ thống giám sát 24/7.

## 5. Cấu hình PAI cho SP4000

Ví dụ `/etc/pai/pai.conf`:

```python
import logging

CONNECTION_TYPE = "Serial"
SERIAL_PORT = "/dev/serial/by-id/usb-REPLACE_WITH_REAL_ADAPTER_ID"
SERIAL_BAUD = 9600
SERIAL_ENCRYPTED = False

# PC Communication Password của BabyWare, không phải master/user/installer code.
PASSWORD = "0000"

# PAI poll trạng thái đầy đủ mỗi 10 giây.
KEEP_ALIVE_INTERVAL = 10
IO_TIMEOUT = 0.5

# Để trống: lúc load definition, PAI tự giữ các zone/PGM đang enabled.
LIMITS = {}

LABEL_ENCODING = "paradox-en"
SYNC_TIME = False

LOGGING_LEVEL_CONSOLE = logging.INFO
LOGGING_DUMP_PACKETS = False
LOGGING_DUMP_MESSAGES = False
LOGGING_DUMP_STATUS = False

# Không bắt buộc nếu dùng SafeLink interface trực tiếp trong PAI.
MQTT_ENABLE = False
```

Lưu ý:

- `PASSWORD` là PC Password 4 ký tự hex trong phần PC Communication của BabyWare.
- Không dùng user code keypad hoặc installer code thay cho PC Password.
- Sai password nhiều lần có thể làm panel lock kết nối trong một khoảng thời gian.
- Với một số firmware, password chứa `0` có thể cần cách encode đặc biệt mà Wiki mô tả, nhưng chỉ thử sau khi đã xác nhận password/baud/wiring đúng.
- `SERIAL_ENCRYPTED=False` là lựa chọn khởi đầu đúng cho SP4000 5.x. Tài liệu `docs/serial_encryption.md` tập trung EVO firmware mới; không tự bật AES cho SP4000 nếu chưa có bằng chứng frame `E0 FE` đúng loại.

## 6. Flow khởi động chi tiết: từ byte serial đến đúng adapter SP4000

### Bước 1 — Start process

```text
pai-service -c /etc/pai/pai.conf
    ↓
paradox.main.main()
    ↓
Paradox() + InterfaceManager
```

### Bước 2 — Mở serial

`Paradox.connection` thấy `CONNECTION_TYPE="Serial"` và tạo:

```text
SerialCommunication(port, baud)
    ↓
serial_asyncio.create_serial_connection()
    ↓
SerialConnectionProtocol
    ↓
SerialFramer
```

Trước khi mở port, code kiểm tra thiết bị có quyền `R_OK | W_OK`.

### Bước 3 — Handshake generic

Lúc đầu PAI chưa biết tủ là EVO hay SP/MG. PAI dùng panel generic để:

1. Gửi `InitiateCommunication`.
2. Nhận model, firmware và serial number.
3. Gửi `StartCommunication`.
4. Đọc `product_id` từ reply.

Trong code, `SPECTRA_SP4000` có product ID `26`. Factory `paradox/hardware/__init__.py` map ID này vào:

```text
paradox.hardware.spectra_magellan.Panel
```

### Bước 4 — Chuyển framing sang SP/MG

Adapter SP/MG được tạo với `variable_message_length=False`. Từ đây `SerialFramer` xử lý frame legacy cố định 37 byte.

Mỗi frame phải qua checksum:

```text
checksum = sum(all_previous_bytes) % 256
```

Nếu checksum sai, framer bỏ một byte để resync thay vì nuốt cả frame kế tiếp.

### Bước 5 — Đăng nhập PC Password

PAI encode password thành 2 byte hex và gửi `InitializeCommunication` command `0x00`. Kết quả chính:

- Reply `0x10`: authentication thành công.
- Close/error `0x70` hoặc `0x00`: authentication thất bại.

Chỉ sau khi authentication thành công, PAI mới load memory và chạy poll status.

### Bước 6 — Load cấu hình zone từ EEPROM

PAI đọc zone definition tại vùng:

```text
0x730 .. <0x7A0, step 0x03, mỗi zone 3 byte
```

Mỗi zone có các trường quan trọng:

- `definition`: disabled, delay, follow, instant, fire, keyswitch...
- `partition`: partition gán cho zone.
- `options.auto_zone_shutdown`.
- `options.bypassable`.
- `options.rf_supervision`.
- `options.alarm_type`.
- `options.intellizone`.
- `options.delay_alarm_transmission`.
- `options.force_arming`.

Zone có `definition != disabled` được coi là enabled. `cfg.LIMITS["zone"]` được giao với danh sách enabled này.

### Bước 7 — Load tên zone

PAI đọc label zone tại:

```text
0x010 .. <0x210, 32 label, mỗi label 16 byte
```

Label được decode theo `LABEL_ENCODING`. PAI tạo metadata kiểu:

```json
{
  "id": 1,
  "label": "Front Door",
  "key": "Front_Door"
}
```

### Bước 8 — Chuyển sang RUN

Sau khi definitions và labels hoàn tất:

```text
run_state = RUN
request_status_refresh()
```

PAI poll ngay, không phải chờ hết 10 giây đầu tiên.

## 7. Flow đọc trạng thái zone

### 7.1 Poll định kỳ

Với SP4000, PAI gửi `ReadEEPROM` vào vùng RAM bắt đầu tại `0x8000`:

```text
request address = 0x8000 + block_id
```

Mỗi reply chứa 32 byte data. Firmware SP4000 5.x poll các block `0,1,2,3,4,5`; block `7` chỉ được poll khi firmware major `>= 6` và chủ yếu chứa PGM status.

`KEEP_ALIVE_INTERVAL=10` nghĩa là chu kỳ poll cố gắng bắt đầu mỗi 10 giây. Đây không phải tần suất push SafeLink. SafeLink vẫn push mỗi 60 giây từ snapshot mới nhất.

### 7.2 Block 0 — open, tamper, fire và system trouble

Các bitmap 4 byte tạo 32 boolean:

- `zone_open[1..32]`
- `zone_tamper[1..32]`
- `zone_fire[1..32]`

Bit thấp nhất của byte đầu là zone 1, bit tiếp theo là zone 2... Adapter dùng:

```text
zone_id = byte_index * 8 + bit_index + 1
value = ((byte >> bit_index) & 1) == 1
```

Block này còn chứa system time, điện áp và trouble tổng của panel.

### 7.3 Block 1 — lỗi RF và partition

- `zone_rf_supervision_trouble[1..32]`
- `zone_rf_low_battery_trouble[1..32]`
- trạng thái 2 partition: alarm, arm, stay/sleep/force, entry/exit delay, ready...

Các cờ RF chỉ có ý nghĩa với zone/module RF tương ứng. Không diễn giải `False` là cảm biến có dây đang “khỏe RF”.

### 7.4 Block 2 — trạng thái chi tiết từng zone

Mỗi zone dùng 1 byte:

| Bit/mask | Field PAI | Ý nghĩa |
|---|---|---|
| `0x80` | `was_in_alarm` | Có alarm trong memory |
| `0x40` | `alarm` | Đang alarm |
| `0x30` | `fire_delay` | Fire delay pattern |
| `0x10` | `entry_delay` | Entry delay |
| `0x08` | `bypassed` | Bị bypass |
| `0x04` | `shutdown` | Zone shutdown |
| `0x02` | `in_tx_delay` | Đang delay transmission |
| `0x01` | `was_bypassed` | Đã từng bypass |

`no_delay` được suy ra khi hai bit `0x30` đều bằng 0.

> Cảnh báo code: biểu thức hiện tại của `intellizone_delay` là `(value & 0x20) == 0x10`, điều kiện này không thể đúng. Không dùng field này cho quyết định nghiệp vụ cho tới khi sửa mask và kiểm tra với frame thật.

### 7.5 Block 3 — signal strength

`zone_signal_strength[1..32]` là số `0..255`. Không tự quy đổi thành dBm hoặc phần trăm nếu chưa có công thức được xác nhận cho model/module RF thực tế.

### 7.6 Block 5 — exit delay theo zone

Bitmap `zone_exit_delay[1..32]` được normalize thành `zone[id].exit_delay`.

### 7.7 Normalize dữ liệu

Các root key phẳng được đổi thành cấu trúc theo element:

```text
zone_open[1]                  → status["zone"][1]["open"]
zone_tamper[1]                → status["zone"][1]["tamper"]
zone_status[1]["alarm"]      → status["zone"][1]["alarm"]
zone_signal_strength[1]       → status["zone"][1]["signal_strength"]
zone_exit_delay[1]            → status["zone"][1]["exit_delay"]
```

Sau đó flow là:

```text
RAM block replies
  → deep_merge
  → convert_raw_status()
  → áp dụng LIMITS
  → ps.sendMessage("status_update")
  → MemoryStorage.update_container_object()
  → Change / ChangeEvent
  → interface bên ngoài
```

## 8. Live event và lý do vẫn cần poll

SP4000 có thể gửi frame live event command nibble `0xE` bất kỳ lúc nào. Event chứa:

- thời gian panel;
- `major` event code;
- `minor` thường là zone/user/object liên quan;
- partition;
- module serial;
- label.

Ví dụ các event major quan trọng trong map SP/MG:

| Major | Ý nghĩa |
|---:|---|
| 0 | Zone OK |
| 1 | Zone open |
| 35 | Zone bypass toggled |
| 36 | Zone in alarm |
| 37 | Zone fire alarm |
| 38 | Zone alarm restore |
| 39 | Fire alarm restore |
| 42 | Zone tamper |
| 43 | Zone tamper restore |

Live event giúp giảm latency, nhưng không được dùng làm nguồn duy nhất vì:

- Có thể mất byte/frame khi serial nhiễu hoặc process restart.
- Event là delta, không phải snapshot đầy đủ.
- Sau reconnect cần poll để tái tạo trạng thái thật.

Thiết kế đúng là: live event để phản ứng nhanh, RAM poll để đối soát và phục hồi trạng thái.

## 9. Ví dụ đầy đủ với zone cửa chính

Giả sử zone 1 tên `Front Door`, thuộc partition 1:

1. Khi PAI khởi động, EEPROM trả definition và label của zone 1.
2. RAM block 0 trả bit `zone_open[1]=False`.
3. RAM block 2 trả `alarm=False`, `bypassed=False`, `was_in_alarm=False`.
4. Snapshot ghi zone 1 là `normal`.
5. Người dùng mở cửa khi tủ đang disarm:
   - panel có thể đẩy live event `Zone open`;
   - lần poll tiếp theo xác nhận `open=True`;
   - `alarm` vẫn có thể `False` vì mở cửa không đồng nghĩa alarm.
6. Nếu partition đang arm và điều kiện alarm xảy ra:
   - `alarm=True`;
   - `was_in_alarm=True` có thể được giữ lại sau đó cho tới khi memory được clear.
7. Khi cửa đóng lại, `open=False`; không được tự xóa `was_in_alarm` nếu panel vẫn giữ memory.

## 10. Contract snapshot đề xuất cho SafeLink

Endpoint `/crawl` hiện nhận một JSON bất kỳ, vì vậy cần chốt schema phía gửi để dữ liệu có thể dùng lâu dài.

```json
{
  "schema_version": 1,
  "kind": "zone_snapshot",
  "snapshot_id": "pi-orange-001:2026-10-15T16:30:00Z",
  "timestamp": "2026-10-15T16:30:00Z",
  "pi_id": "pi-orange-001",
  "full": true,
  "fresh": true,
  "panel": {
    "vendor": "Paradox",
    "model": "SP4000",
    "serial_number": "01234567",
    "firmware": "5.20 build 0",
    "connection": "serial",
    "serial_baud": 9600
  },
  "source_health": {
    "pai_run_state": "RUN",
    "last_panel_status_at": "2026-10-15T16:29:54Z",
    "status_age_seconds": 6,
    "zone_count": 12
  },
  "zones": [
    {
      "id": 1,
      "key": "Front_Door",
      "name": "Front Door",
      "partition": 1,
      "definition": "delay_1",
      "status": "normal",
      "open": false,
      "alarm": false,
      "was_in_alarm": false,
      "tamper": false,
      "fire": false,
      "bypassed": false,
      "shutdown": false,
      "entry_delay": false,
      "exit_delay": false,
      "rf_supervision_trouble": false,
      "rf_low_battery_trouble": false,
      "signal_strength": 0
    }
  ],
  "summary": {
    "normal": 12,
    "open": 0,
    "alarm": 0,
    "tamper": 0,
    "fire": 0,
    "bypassed": 0,
    "stale": 0
  }
}
```

### 10.1 Quy tắc sinh `status`

Không bỏ các boolean gốc. `status` chỉ là field tổng hợp để UI/filter nhanh, theo độ ưu tiên:

```text
stale/unknown
  > fire
  > alarm
  > tamper
  > rf_supervision_trouble hoặc rf_low_battery_trouble
  > bypassed
  > open
  > normal
```

Một zone có thể đồng thời `open=true` và `bypassed=true`; JSON vẫn phải giữ cả hai cờ.

### 10.2 “Toàn bộ zone” nghĩa là gì

Mặc định nên gửi toàn bộ zone đang `enabled` trong EEPROM. Nếu SafeLink cần đủ slot 1..32, mỗi slot disabled phải có:

```json
{"id": 13, "enabled": false, "status": "disabled"}
```

Không được gửi zone disabled thành `open=false`/`normal`, vì đó là trạng thái giả.

## 11. Interface push 60 giây — thiết kế khuyến nghị

Nên thêm một interface đúng kiến trúc PAI, ví dụ:

```text
paradox/interfaces/safelink_crawl.py
    SafeLinkCrawlInterface(AsyncInterface)
```

Interface đăng ký các topic nội bộ:

- `panel_detected`
- `definitions_loaded`
- `labels_loaded`
- `status_update`
- `run-state`

Luồng xử lý:

```mermaid
sequenceDiagram
    participant Panel as SP4000
    participant PAI as PAI poll loop
    participant Cache as Snapshot cache
    participant Push as SafeLink worker
    participant API as /core/webhook/ultrasync/crawl

    loop Mỗi khoảng 10 giây
        PAI->>Panel: Đọc RAM blocks 0,1,2,3,4,5
        Panel-->>PAI: Status replies
        PAI->>Cache: Ghi đè snapshot mới nhất + last_status_at
    end

    loop Mỗi 60 giây
        Push->>Cache: Deep-copy snapshot nhất quán
        Push->>Push: Kiểm tra freshness và build JSON
        Push->>API: HTTPS POST với timeout hữu hạn
        API-->>Push: HTTP 2xx hoặc lỗi
    end
```

Nguyên tắc production:

1. Callback `status_update` chỉ copy dữ liệu, không gọi HTTP.
2. HTTP chạy ở worker riêng hoặc `asyncio.to_thread`; tuyệt đối không block serial/event loop.
3. Dùng connect timeout khoảng 5 giây, read timeout khoảng 10 giây.
4. Retry có exponential backoff và jitter, ví dụ 1 s, 2 s, 4 s; không retry vô hạn trong cùng chu kỳ.
5. Queue kiểu “latest only”: khi mạng lỗi, giữ snapshot mới nhất thay vì chất vô hạn 60 snapshot cũ.
6. `snapshot_id` phải ổn định cho một snapshot để backend có thể bổ sung idempotency sau này.
7. Chỉ gửi lần đầu sau khi đã có `panel_detected`, labels/definitions và status đầu tiên.
8. Nếu không có status mới quá 30 giây, đặt `fresh=false`, `status=stale`; không biến trạng thái cuối cùng thành `normal`.
9. Secret lấy từ environment/systemd credential, không hard-code và không log.
10. Log token phải được redact.

Config đề xuất:

```python
SAFELINK_CRAWL_ENABLE = True
SAFELINK_CRAWL_URL = "https://safelinksystem.app/core/webhook/ultrasync/crawl"
SAFELINK_PI_ID = "pi-orange-001"
SAFELINK_PUSH_INTERVAL = 60
SAFELINK_STALE_AFTER = 30
SAFELINK_CONNECT_TIMEOUT = 5
SAFELINK_READ_TIMEOUT = 10
SAFELINK_X_TOKEN = None  # lấy từ biến môi trường, không commit secret
```

Repo đã có dependency `requests`, do đó không cần thêm HTTP library mới. Tuy nhiên lời gọi `requests.post()` phải chạy ngoài asyncio loop.

### 11.1 Phương án thay thế: MQTT sidecar

Nếu hệ thống đã có Mosquitto và muốn cô lập process HTTP khỏi PAI hoàn toàn:

- PAI publish retained state lên `paradox/states/zones/<zone_key>/<property>`.
- Bật `MQTT_PUBLISH_DEFINITIONS=True` để lấy `id`, definition và partition từ `paradox/definitions/zones/...`.
- Một service riêng subscribe retained topics, dựng cache và push mỗi 60 giây.

Ưu điểm là lỗi bridge không ảnh hưởng PAI. Nhược điểm là thêm broker/process và việc lấy label gốc/atomic snapshot phức tạp hơn. Nếu chưa có MQTT, interface trực tiếp là lựa chọn gọn hơn.

## 12. Trạng thái thật của endpoint SafeLink hiện tại

Code local hiện tại:

```java
@RequestMapping("/webhook/ultrasync")
...
@PostMapping("/crawl")
public void receivedInstallerCode(@RequestBody Object obj) {
    CrawlEntity crawlEntity = new CrawlEntity();
    crawlEntity.setId(generateId());
    crawlEntity.setData(StringCommon.writeAsString(obj));
    crawlRepository.save(crawlEntity);
}
```

Với `server.servlet.context-path=/core`, URL đầy đủ là:

```text
POST https://safelinksystem.app/core/webhook/ultrasync/crawl
Content-Type: application/json
```

Các giới hạn phải xử lý trước production:

1. `/crawl` hiện chỉ lưu raw JSON vào bảng `nxg8_crawl`; nó không parse hoặc upsert zone state cho UI.
2. `/event` có kiểm tra `x-token`, nhưng `/crawl` hiện không có kiểm tra token.
3. Security config cho phép `/webhook/ultrasync/**`; vì vậy `/crawl` hiện có thể bị ghi dữ liệu rác từ Internet.
4. Entity khai báo `String data` nhưng source không có migration/DDL của `nxg8_crawl`; chưa chứng minh cột đủ chứa JSON 32 zone. Cần xác nhận là `TEXT` hoặc `JSONB`, không phải `VARCHAR(255)`.
5. Endpoint tạo row mới cho mọi lần POST, không có idempotency và retention.
6. Một Orange Pi gửi mỗi phút tạo 1.440 row/ngày, khoảng 525.600 row/năm. N thiết bị nhân N lần.
7. Method name/log hiện ghi “installer code”, không đúng semantics zone snapshot.
8. Controller log toàn bộ JSON ở mức `INFO`; label zone và trạng thái an ninh có thể đi vào log, làm tăng dung lượng và rủi ro lộ dữ liệu.
9. Response hiện là `void`; HTTP 2xx chỉ chứng minh row đã được lưu, không chứng minh dữ liệu đã được parse hoặc cập nhật vào trạng thái vận hành của SafeLink.

Điều kiện tối thiểu cần chốt ở backend:

- Áp dụng `x-token` hoặc chữ ký HMAC cho `/crawl`.
- Cột payload là `JSONB`/`TEXT` đủ lớn.
- Có index theo `pi_id`, `timestamp` hoặc cột được parse riêng.
- Có retention hoặc bảng “latest snapshot” upsert theo `pi_id`.
- Nếu SafeLink UI cần trạng thái zone: thêm parser/upsert, không chỉ archive raw JSON.
- Trả response JSON rõ ràng gồm `accepted`, `snapshot_id`, `server_time`.
- Bổ sung idempotency theo `snapshot_id`.

Không đưa token mặc định hoặc credential thật vào tài liệu, repo hay log.

## 13. Cài PAI trên Orange Pi 3

Ví dụ Debian/Ubuntu/Armbian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

sudo mkdir -p /opt/pai /etc/pai /var/log/pai
sudo chown -R "$USER":"$USER" /opt/pai

git clone https://github.com/ParadoxAlarmInterface/pai.git /opt/pai/src
python3 -m venv /opt/pai/venv
/opt/pai/venv/bin/python -m pip install --upgrade pip
/opt/pai/venv/bin/python -m pip install -e /opt/pai/src
```

Repo hiện yêu cầu Python `>=3.8` và khai báo hỗ trợ 3.8–3.14. Wiki Manual còn nhắc Python 3.6/3.7, phần đó đã cũ so với `pyproject.toml` hiện tại.

Tìm serial ổn định:

```bash
ls -l /dev/serial/by-id/
udevadm info --query=property --name=/dev/ttyUSB0
```

Thêm user service vào `dialout`, sau đó đăng xuất/đăng nhập hoặc reboot:

```bash
sudo usermod -aG dialout pai
```

Service nên có:

- `Restart=always`
- `RestartSec=5`
- `After=network-online.target`
- file config mode `0600`
- environment chứa token, không ghi token trong unit public
- log rotation hoặc journald limit

## 14. Checklist kiểm thử hiện trường

### 14.1 Điện và dây

- [ ] Đã đo/xác nhận chân `AUX+` là 12 V và không nối vào Orange Pi.
- [ ] Adapter nhận đúng TTL 5 V, không phải RS232 ±12 V.
- [ ] Panel TX → adapter RX, panel RX ← adapter TX, GND chung.
- [ ] Orange Pi dùng nguồn riêng ổn định.
- [ ] Không có thiết bị/process khác chiếm serial.

### 14.2 Kết nối PAI

- [ ] Serial device tồn tại ở `/dev/serial/by-id/...`.
- [ ] User PAI có read/write permission.
- [ ] Log có `Serial port open`.
- [ ] Log detect đúng `SP4000`, firmware và serial number.
- [ ] Log có `Authentication Success`.
- [ ] Definitions và labels được load.
- [ ] `run_state=RUN` và status poll tiếp tục đều.

### 14.3 Mapping zone

Kiểm tra từng zone vật lý, không chỉ zone 1:

1. Ghi zone ID/label/partition từ BabyWare hoặc keypad.
2. Mở/đóng từng cửa hoặc kích PIR.
3. Xác nhận đúng zone chuyển `open false → true → false`.
4. Kiểm tra tamper riêng.
5. Kiểm tra bypass riêng.
6. Chỉ test alarm/fire theo quy trình an toàn, có người phụ trách tủ và đã cô lập đường báo trung tâm nếu cần.
7. Xác nhận không lệch `zone_id` ±1.

### 14.4 Snapshot 60 giây

- [ ] Snapshot đầu tiên chỉ gửi sau full status đầu tiên.
- [ ] Mỗi payload chứa đủ toàn bộ zone enabled.
- [ ] `timestamp` là UTC ISO-8601.
- [ ] `status_age_seconds` nhỏ hơn ngưỡng stale khi panel khỏe.
- [ ] Mất serial không tạo hàng loạt zone `normal` giả.
- [ ] Mất Internet không block poll serial.
- [ ] Sau phục hồi mạng chỉ gửi latest snapshot, không flood backlog vô hạn.
- [ ] Backend nhận payload lớn hơn trường hợp 32 zone.
- [ ] Token sai bị `401/403` sau khi backend bổ sung auth.
- [ ] Duplicate `snapshot_id` không tạo dữ liệu trùng sau khi backend bổ sung idempotency.

### 14.5 Soak test

Chạy tối thiểu 24–72 giờ và đo:

- số lần reconnect serial;
- checksum/framing error;
- số status poll timeout;
- tuổi dữ liệu lớn nhất;
- HTTP latency p50/p95/p99;
- số POST lỗi/retry;
- CPU, RAM và dung lượng log;
- tốc độ tăng bảng `nxg8_crawl`.

## 15. Các sai khác giữa Wiki và code hiện tại

Khi có xung đột, tài liệu này ưu tiên code ở commit đã nêu:

1. Wiki MQTT có chỗ ghi raw event ở `paradox/states/raw`; code hiện tại publish `paradox/events/raw`.
2. Wiki dùng tên `KEEP_ALIVE_INTERFACE`; config/code hiện tại dùng `KEEP_ALIVE_INTERVAL`.
3. Wiki cũ nói Python 3.6+; `pyproject.toml` hiện yêu cầu Python 3.8+.
4. Wiki nói event 37 byte ở 9600 baud mất khoảng 500 µs. Với 8N1, 37 byte cần xấp xỉ `37 × 10 / 9600 = 38,5 ms`, chưa tính khoảng nghỉ và xử lý.
5. Wiki hiện chưa phản ánh đầy đủ PRT3 và serial encryption mới có trong nhánh repo này; hai phần đó không phải đường chính cho SP4000 5.x.

## 16. Kết luận production

Luồng đúng cho bài toán này là:

```text
SP4000
→ TTL 5 V an toàn
→ USB serial ổn định trên Orange Pi 3
→ PAI handshake + PC Password
→ đọc EEPROM để biết zone nào tồn tại và tên/definition của zone
→ poll RAM 10 giây để có full state
+ live event để giảm latency
→ cache snapshot đầy đủ
→ worker HTTP độc lập push mỗi 60 giây
→ SafeLink xác thực, lưu JSON đủ lớn và upsert/retention đúng mục đích
```

Không nên triển khai production cho tới khi hoàn tất ba việc:

1. Xác nhận phần cứng/điện áp và mapping từng zone trên SP4000 thật.
2. Sửa/hoàn thiện contract backend `/crawl`: auth, payload size, parse/upsert, idempotency và retention.
3. Chạy soak test 24–72 giờ, chứng minh mất serial/mạng không làm phát sinh trạng thái `normal` giả.

## 17. Nguồn tham khảo chính

- [PAI README](https://github.com/ParadoxAlarmInterface/pai)
- [PAI Wiki](https://github.com/ParadoxAlarmInterface/pai/wiki)
- [Connection methods](https://github.com/ParadoxAlarmInterface/pai/wiki/Connection-methods)
- [Compatibility](https://github.com/ParadoxAlarmInterface/pai/wiki/Compatibility)
- [Configuration](https://github.com/ParadoxAlarmInterface/pai/wiki/Configuration)
- [MQTT Interface](https://github.com/ParadoxAlarmInterface/pai/wiki/MQTT-Interface)
- [FAQ](https://github.com/ParadoxAlarmInterface/pai/wiki/FAQ)
- [Paradox SP4000 product page](https://www.paradox.com/Products/CAT_SPECTRA/ControlPanels.asp?CATID=6&PRD=1587&SUBCATID=61)
- [Paradox MG/SP Reference & Installation Guide](https://www.paradox.com/Firmware/MGSP_Installation_Guide_Rev_16.pdf)
- Code chính trong repo: `paradox/paradox.py`, `paradox/connections/serial/*`, `paradox/hardware/spectra_magellan/*`, `paradox/parsers/status.py`, `paradox/data/*`.

