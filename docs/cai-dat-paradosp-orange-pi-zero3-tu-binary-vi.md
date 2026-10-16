# Cài paradosp cho SP4000 trên Orange Pi Zero 3 từ binary

Hướng dẫn này bắt đầu tại thời điểm đã có hai file `paradosp` và
`setup-orange-pi.sh`. Mục tiêu là cài service trên Armbian 64-bit, bật đúng
UART5 trên header 26 pin, kết nối Paradox SP4000 ở 9600 baud và xác nhận API đã
có dữ liệu panel, zone.

Kết quả đã kiểm tra trên máy `demo-pi-0003`:

- SoC H616/H618 dùng controller `5001400.serial` cho UART5.
- Sau khi overlay được nạp đúng, UART5 xuất hiện là `/dev/ttyS1` trên máy này.
- `/dev/ttyS0` trỏ tới `5000000.serial`, là UART0/debug console, không phải
  UART5.
- PAI kết nối SP4000 và API trả dữ liệu sau khi chuyển `SERIAL_PORT` sang UART5.

> Không chọn cổng chỉ dựa vào tên `/dev/ttyS1` hoặc `/dev/ttyS5`. Luôn chọn
> thiết bị trỏ tới controller `5001400.serial`.

## 1. Điều kiện trước khi cài

Cần chuẩn bị:

- Orange Pi Zero 3 chạy Armbian 64-bit và truy cập được bằng SSH.
- File binary ARM64 `paradosp`.
- File `setup-orange-pi.sh` trong thư mục `deploy/orange-pi` của dự án.
- Mạch chuyển mức phù hợp giữa Serial TTL 5 V của SP4000 và GPIO 3,3 V của
  Orange Pi.
- PC Communication Password của SP4000 gồm đúng 4 ký tự hexadecimal.

Không nối trực tiếp tín hiệu TX 5 V của SP4000 vào chân RX 3,3 V của Orange Pi.
Không nối chân `AUX+` 12 V của SP4000 vào Orange Pi.

## 2. Chép binary và script lên Orange Pi

Nếu hai file chưa nằm trên Orange Pi, từ máy phát hành chạy:

```bash
scp paradosp setup-orange-pi.sh admini@IP_ORANGE_PI:~/
```

SSH vào Orange Pi:

```bash
ssh admini@IP_ORANGE_PI
```

Kiểm tra file và kiến trúc:

```bash
cd ~
ls -lh paradosp setup-orange-pi.sh
uname -m
```

Kết quả `uname -m` bắt buộc là:

```text
aarch64
```

Có thể chạy self-test trước khi cài:

```bash
chmod +x paradosp
./paradosp --self-test
```

Kết quả đạt có dạng:

```text
paradosp self-test OK: platform=Linux arch=aarch64 python=...
```

## 3. Cài service paradosp

Chạy script với quyền root:

```bash
sudo sh setup-orange-pi.sh ./paradosp
```

Script thực hiện các việc sau:

- Cài binary tại `/opt/paradosp/paradosp`.
- Tạo cấu hình tại `/etc/paradosp/paradosp.conf` nếu file chưa tồn tại.
- Tạo thư mục state `/var/lib/paradosp`.
- Tạo thư mục log `/var/log/paradosp`.
- Cấu hình sẵn Serial `/dev/ttyS1`, 9600 baud, không mã hóa và PC Password
  mặc định `0000`.
- Cấu hình log xoay theo ngày, nén gzip và giữ 30 file cũ.
- Tạo và enable `paradosp.service`.
- Chạy self-test rồi khởi động service.

Kiểm tra service:

```bash
sudo systemctl status paradosp --no-pager
```

Nếu UART5 đã bật và PC Communication Password của tủ là `0000`, không cần gọi
Postman hoặc API cấu hình Serial. Nếu password khác, sửa trực tiếp file cấu
hình như mục 7.

## 4. Bật UART5 trên Armbian

Sao lưu boot environment:

```bash
sudo cp -a /boot/armbianEnv.txt /boot/armbianEnv.txt.bak-uart5
```

Mở file:

```bash
sudo nano /boot/armbianEnv.txt
```

Đảm bảo có các dòng:

```text
console=none
overlay_prefix=sun50i-h616
overlays=uart5
```

Quy tắc quan trọng:

- Khi `overlay_prefix=sun50i-h616`, giá trị trong `overlays` chỉ là `uart5`.
- Không đặt `overlays=sun50i-h616-uart5`.
- Nếu đã có overlay khác, giữ chúng và thêm `uart5`, phân cách bằng dấu cách.

Ví dụ:

```text
overlays=i2c3 uart5
```

Kiểm tra file overlay có trên máy:

```bash
sudo find /boot -type f -name '*uart5*.dtbo' -print
```

Trên image đã kiểm tra có file:

```text
sun50i-h616-uart5.dtbo
```

Tắt login service trên UART0 để tránh Linux tiếp tục dùng cổng debug:

```bash
sudo systemctl mask --now serial-getty@ttyS0.service
sudo systemctl daemon-reload
```

Khởi động lại để áp dụng device-tree overlay:

```bash
sudo reboot
```

## 5. Xác định đúng thiết bị UART5

Sau khi máy khởi động lại, SSH vào và kiểm tra model:

```bash
cat /proc/device-tree/model
echo
```

Kiểm tra UART5:

```bash
sudo dmesg | grep -Ei '5001400|ttyS'
```

Kết quả đúng phải có controller `5001400.serial`, ví dụ:

```text
5001400.serial: ttyS1 at MMIO 0x5001400 ... is a 16550A
```

Xác định mapping chính xác giữa device và controller:

```bash
for t in /sys/class/tty/ttyS*; do
  printf '%s -> ' "/dev/${t##*/}"
  readlink -f "$t/device"
done
```

Kết quả trên máy đã kiểm tra:

```text
/dev/ttyS0 -> .../5000000.serial/...
/dev/ttyS1 -> .../5001400.serial/...
```

Vì vậy máy này dùng:

```text
/dev/ttyS1
```

Nếu không có bất kỳ device nào trỏ tới `5001400.serial`, overlay UART5 chưa
được áp dụng. Kiểm tra lại `overlay_prefix`, `overlays`, file `.dtbo` và reboot
trước khi tiếp tục.

Xác nhận UART5 không bị process khác giữ:

```bash
systemctl is-active serial-getty@ttyS1.service
sudo fuser -v /dev/ttyS1
```

Khi PAI đang dừng, kết quả mong muốn là `inactive` và `fuser` không có output.

Kiểm tra kernel có cấu hình được UART ở 9600 baud:

```bash
sudo stty -F /dev/ttyS1 9600 cs8 -cstopb -parenb
```

Không có output lỗi là đạt.

## 6. Đấu dây SP4000 với Orange Pi Zero 3

Với UART5 trên header 26 pin:

```text
SP4000 TX  -> level shifter 5 V sang 3,3 V -> Orange Pi pin 10, UART5 RX
SP4000 RX  <- level shifter 3,3 V sang 5 V <- Orange Pi pin 8, UART5 TX
SP4000 GND <------------------------------> Orange Pi pin 6, GND
```

Kiểm tra trước khi cấp tín hiệu:

- TX và RX đã đấu chéo.
- GND đã nối chung theo thiết kế của mạch chuyển mức hoặc mạch cách ly.
- Tín hiệu đi vào Orange Pi RX không vượt 3,3 V.
- Không có BabyWare, WinLoad, IP150 hoặc thiết bị Serial khác cùng giữ phiên
  giao tiếp với SP4000.

## 7. Cấu hình Serial không cần Postman

Ở lần cài mới, script đã tạo sẵn:

```python
CONNECTION_TYPE = "Serial"
SERIAL_PORT = "/dev/ttyS1"
SERIAL_BAUD = 9600
SERIAL_ENCRYPTED = False
PASSWORD = "0000"
```

Nếu PC Communication Password thật của tủ không phải `0000`, mở file:

```bash
sudo nano /etc/paradosp/paradosp.conf
```

Sửa duy nhất dòng `PASSWORD`, lưu file rồi chạy:

```bash
sudo systemctl restart paradosp
```

`PASSWORD` là PC Communication Password trong BabyWare, không phải installer
code, master code hoặc user code trên keypad.

Với SP4000 thông thường, giữ:

```json
"SERIAL_BAUD": 9600,
"SERIAL_ENCRYPTED": false
```

Nếu trước đây đã từng cấu hình bằng API/Postman, file sau sẽ được ưu tiên hơn
cấu hình mặc định:

```text
/var/lib/paradosp/.pai/safelink-serial.json
```

Đọc lại cấu hình đang chạy để xác nhận:

```bash
curl http://127.0.0.1:8082/api/v1/config/serial
```

API hiện trả cả password rõ. Không đăng response lên nơi công khai và không mở
port `8082` ra Internet.

## 8. Xác nhận kết nối SP4000

Theo dõi log:

```bash
sudo journalctl -u paradosp -f
```

Luồng thành công có các dòng tương tự:

```text
Connection: Serial(/dev/ttyS1@9600)
Connecting to serial port /dev/ttyS1
Serial port open
Connecting to Panel
Connected via Serial(/dev/ttyS1@9600) to SP4000 version ...
Installer login
Authentication Success
Connection OK
Loading data from panel memory
Running
```

Nếu đang bật packet debug, phải thấy cả hai chiều:

```text
PAI -> SER ...
SER -> PAI ...
```

Kiểm tra API:

```bash
curl http://127.0.0.1:8082/api/v1/health
curl http://127.0.0.1:8082/api/v1/panel
curl http://127.0.0.1:8082/api/v1/zones
```

Trạng thái đạt:

- Health có `run_state` là `RUN`.
- `panel_detected` là `true`.
- `/panel` có model, firmware và serial number.
- `/zones` có dữ liệu zone hiện tại.

Heartbeat gửi ngay khi nhận được snapshot zone đầu tiên, sau đó gửi mỗi 60
giây. Kiểm tra trực tiếp:

```bash
curl http://127.0.0.1:8082/api/v1/health
```

Các field cần tăng hoặc có giá trị:

```text
heartbeat_push_count
last_heartbeat_at
```

Trong journal sẽ có dòng:

```text
SafeLink heartbeat delivered: N zone(s)
```

Tên JSON hiện vẫn là `heatbeath` để tương thích với contract cũ. `typeevent`
là sự kiện thay đổi tức thời như zone mở/đóng, arm/disarm, alarm hoặc trouble.
Các mẫu điện áp DEBUG định kỳ (`battery`, `dc`, `vdc`) bị lọc khỏi
`typeevent`; cảnh báo nguồn/battery mức CRITICAL vẫn được gửi.

## 9. Log theo ngày và nén gzip

Log hiện tại:

```text
/var/log/paradosp/paradosp.log
```

Qua nửa đêm, file cũ có dạng:

```text
/var/log/paradosp/paradosp.log.2026-10-16.gz
```

Mặc định giữ 30 file log cũ. Xem log hiện tại và log đã nén:

```bash
sudo tail -f /var/log/paradosp/paradosp.log
sudo zless /var/log/paradosp/paradosp.log.2026-10-16.gz
```

## 10. Chẩn đoán theo dấu hiệu log

| Dấu hiệu | Kết luận gần nhất | Việc cần làm |
|---|---|---|
| `Serial port open` nhưng chỉ có `PAI -> SER` | Linux mở được device nhưng chưa nhận frame hợp lệ | Kiểm tra có đang dùng đúng device trỏ tới `5001400.serial`, sau đó kiểm tra TX/RX/GND và level shifter |
| `/dev/ttyS0 -> 5000000.serial` | Đang dùng UART0, không phải UART5 | Không cấu hình PAI bằng `ttyS0`; tìm device trỏ tới `5001400.serial` |
| Không có `5001400.serial` sau reboot | Overlay UART5 chưa được nạp | Dùng `overlay_prefix=sun50i-h616` và `overlays=uart5`, rồi reboot |
| `serial-getty@ttyS0` active | Linux đang dùng UART0 làm login console | Đặt `console=none`, mask `serial-getty@ttyS0`, rồi reboot |
| `Authentication Failed` hoặc `Invalid PC Password` | Đã giao tiếp được với panel nhưng sai PC Password | Kiểm tra PC Communication Password trong BabyWare; không thử liên tục nhiều mã |
| Có `Authentication Success` nhưng chưa có zone | Login thành công, lỗi nằm ở bước đọc memory/status | Xem các log ngay sau `Loading data from panel memory` |
| API có panel và zone | Chuỗi UART, protocol và login đã hoạt động | Chuyển sang kiểm thử zone thực tế và SafeLink webhook |

Dòng `Unable to connect to alarm via Serial(...)` là lỗi tổng quát. Luôn đọc
các dòng ngay trước nó để xác định lỗi mở device, handshake, authentication hay
đọc memory.

## 11. Bật và tắt debug Serial

Mở file:

```bash
sudo nano /etc/paradosp/paradosp.conf
```

Thêm hoặc cập nhật:

```python
import logging

LOGGING_LEVEL_CONSOLE = logging.DEBUG
LOGGING_DUMP_PACKETS = True
LOGGING_DUMP_MESSAGES = True
```

Áp dụng:

```bash
sudo systemctl restart paradosp
sudo journalctl -u paradosp -f
```

Log packet có thể chứa PC Password hoặc dữ liệu tủ. Sau khi kiểm tra xong, trả
về:

```python
LOGGING_LEVEL_CONSOLE = logging.INFO
LOGGING_DUMP_PACKETS = False
LOGGING_DUMP_MESSAGES = False
```

Rồi restart service:

```bash
sudo systemctl restart paradosp
```

## 12. Cập nhật binary về sau

Chép binary `paradosp` mới và chạy lại cùng script:

```bash
sudo sh setup-orange-pi.sh ./paradosp
```

Script thay binary bằng file mới, tạo lại unit systemd và restart service. Nó
không ghi đè giá trị đã có trong `/etc/paradosp/paradosp.conf`; khi nâng cấp từ
bản cũ, nó chỉ bổ sung các khóa log theo ngày còn thiếu. State và cấu hình
Serial trong `/var/lib/paradosp` được giữ lại.

Sau cập nhật, xác nhận lại:

```bash
sudo systemctl status paradosp --no-pager
sudo journalctl -u paradosp -n 100 --no-pager
curl http://127.0.0.1:8082/api/v1/health
curl http://127.0.0.1:8082/api/v1/zones
```

## 13. Checklist bàn giao

- [ ] `uname -m` trả `aarch64`.
- [ ] `paradosp --self-test` thành công.
- [ ] `paradosp.service` ở trạng thái `active`.
- [ ] `/boot/armbianEnv.txt` có `overlay_prefix=sun50i-h616` và
  `overlays=uart5`.
- [ ] UART5 xuất hiện dưới controller `5001400.serial`.
- [ ] PAI dùng đúng device ánh xạ tới `5001400.serial`; trên máy đã kiểm tra là
  `/dev/ttyS1`.
- [ ] Serial chạy ở 9600 baud, 8 data bits, no parity, 1 stop bit.
- [ ] SP4000 TX/RX đấu chéo qua level shifter và có GND chung.
- [ ] Log có `Authentication Success` và `Running`.
- [ ] `/api/v1/health` có `run_state=RUN`.
- [ ] `/api/v1/panel` và `/api/v1/zones` trả dữ liệu thật.
- [ ] `heartbeat_push_count` tăng và `last_heartbeat_at` có giá trị.
- [ ] Có file `/var/log/paradosp/paradosp.log`; file ngày cũ có đuôi `.gz`.
- [ ] Đã tắt packet debug sau khi nghiệm thu.
- [ ] Port `8082` chỉ truy cập từ mạng quản trị tin cậy.
