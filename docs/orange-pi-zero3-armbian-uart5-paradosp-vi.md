# Orange Pi Zero 3 (Armbian) + UART5 + Paradox SP4000 + `paradosp`

Tài liệu này là các bước đã kiểm tra theo máy thực tế:

```text
Orange Pi Zero 3
Armbian 25.5.1 stable
Linux 6.12.23-current-sunxi64
GPIO UART5: chân 8 / 10 trên header 26 chân
Kernel map UART5 thành: /dev/ttyS0
```

> Quan trọng: `/dev/ttyS0` trong tài liệu này là kết quả của image Armbian trên. Với image/kernel khác, phải xác minh lại bằng bước 6, không được đoán chỉ dựa theo tên `/dev/ttyS5`.

## 1. Mục tiêu cuối cùng

Sau khi hoàn thành, `paradosp` kết nối SP4000 qua UART5 GPIO, nhận zone/live event và vẫn cung cấp API SafeLink tại `http://<IP-Orange-Pi>:8082`.

```text
SP4000 serial TTL 5 V
  -> level shifter / isolator phù hợp
  -> Orange Pi Zero 3 UART5 GPIO (pin 8, 10, 6)
  -> /dev/ttyS0 trên Armbian hiện tại
  -> paradosp
```

## 2. Cảnh báo phần cứng bắt buộc

SP4000 dùng serial TTL 5 V, GPIO Orange Pi dùng 3,3 V.

- Không nối trực tiếp `SP4000 TX` vào RX Orange Pi.
- Phải có level shifter/cách ly phù hợp ở chiều `SP4000 TX (5 V) -> Orange Pi RX (3,3 V)`.
- Không nối chân `AUX+ 12 V` của SP4000 vào Orange Pi.
- Orange Pi và SP4000 phải có GND tín hiệu chung qua mạch chuyển mức/cách ly theo thiết kế của mạch đó.

Sơ đồ tín hiệu:

```text
SP4000 TX  -> level shifter -> Orange Pi pin 10 (UART5 RX)
SP4000 RX  <- level shifter <- Orange Pi pin 8  (UART5 TX)
SP4000 GND <----------------> Orange Pi pin 6  (GND)
```

## 3. Cài `paradosp`

Copy hai file ARM64 build từ CI vào cùng một thư mục trên Orange Pi:

```text
paradosp
setup-orange-pi.sh
```

Cài bằng một lệnh:

```bash
sudo sh setup-orange-pi.sh ./paradosp
```

Service tự chạy sau khi cài:

```bash
sudo systemctl status paradosp
```

Lần chạy đầu, trước khi API nhận cấu hình serial, chương trình có thể thử default `/dev/ttyS5`. Điều này không làm service/API chết; service sẽ retry. Các bước tiếp theo sẽ xác định rồi lưu port thật.

## 4. Bật UART5 trên Armbian

Mở công cụ cấu hình:

```bash
sudo armbian-config
```

Đi theo menu:

```text
System -> Kernel -> Manage device tree overlays
```

Trong danh sách, chọn đúng:

```text
sun50i-h616-uart5
```

Thao tác giao diện:

```text
Mui ten len/xuong : di chuyen
Space              : tick [*] sun50i-h616-uart5
Tab                : chuyen focus den <Save>
Enter              : luu
```

Không chọn các overlay `sun50i-a64-uart*`, `sun50i-h5-uart*`, PWM, SPI hay I2C.

Kiểm tra cấu hình đã lưu:

```bash
grep -E '^(overlay_prefix|overlays)=' /boot/armbianEnv.txt
```

Kết quả cần có:

```text
overlay_prefix=sun50i-h616
overlays=sun50i-h616-uart5
```

Có thể kiểm tra file overlay:

```bash
sudo find /boot -type f -name 'sun50i-h616-uart5.dtbo' -print
```

## 5. Tắt serial console vì UART5 đang map thành `ttyS0`

Trên kernel Armbian nêu trên, UART5 xuất hiện là `/dev/ttyS0`. Armbian lại dùng sẵn `ttyS0` làm boot/login console ở 115200 baud. Nếu không tắt, kernel có thể gửi boot log vào SP4000 và gây lỗi giao tiếp.

Sao lưu boot environment:

```bash
sudo cp -a /boot/armbianEnv.txt /boot/armbianEnv.txt.bak
```

Đổi `console=both` thành `console=none`:

```bash
sudo sed -i -E 's/^console=.*/console=none/' /boot/armbianEnv.txt
```

Tắt dịch vụ login serial nếu đang có:

```bash
sudo systemctl disable --now serial-getty@ttyS0.service
```

Xác nhận:

```bash
grep '^console=' /boot/armbianEnv.txt
```

Kết quả cần là:

```text
console=none
```

Reboot để áp dụng overlay và bỏ serial console:

```bash
sudo reboot
```

> Vẫn quản trị Orange Pi bằng SSH. Nếu cần phục hồi serial console, chép lại file `/boot/armbianEnv.txt.bak` thành `/boot/armbianEnv.txt`, rồi reboot.

## 6. Xác nhận đúng port sau reboot

Sau khi SSH vào lại, kiểm tra kernel map UART nào:

```bash
sudo dmesg | grep -Ei 'ttyS[0-9]|uart'
```

Trên máy đã kiểm tra, có dòng tương tự:

```text
5000000.serial: ttyS0 at MMIO ... is a 16550A
```

Kiểm tra Linux có thể cấu hình UART ở 9600 baud:

```bash
sudo stty -F /dev/ttyS0 9600 cs8 -cstopb -parenb
```

Không có output lỗi là đạt. Nếu còn `Input/output error`, dừng ở đây: overlay chưa được kernel áp dụng đúng; không phải lỗi password, PAI hay dây SP4000.

## 7. Lưu cấu hình serial cho PAI

Sau reboot, service tự start. Kiểm tra API:

```bash
curl http://127.0.0.1:8082/api/v1/health
```

Gửi cấu hình qua API. `PASSWORD` là **PC Communication Password của BabyWare**, không phải installer/master/user code:

```bash
curl -X PUT http://127.0.0.1:8082/api/v1/config/serial \
  -H 'Content-Type: application/json' \
  -d '{
    "CONNECTION_TYPE": "Serial",
    "SERIAL_PORT": "/dev/ttyS0",
    "SERIAL_BAUD": 9600,
    "SERIAL_ENCRYPTED": false,
    "PASSWORD": "0000"
  }'
```

API lưu cấu hình tại:

```text
/var/lib/paradosp/.pai/safelink-serial.json
```

Vì vậy reboot sau này sẽ tự đọc lại `/dev/ttyS0`; không cần gọi lại API.

Kiểm tra cấu hình đã lưu qua API:

```bash
curl http://127.0.0.1:8082/api/v1/config/serial
```

## 8. Khởi động và theo dõi PAI

Nếu service chưa chạy:

```bash
sudo systemctl start paradosp
```

Theo dõi log:

```bash
sudo journalctl -u paradosp -f
```

Mốc cần thấy:

```text
Connection: Serial(/dev/ttyS0@9600)
```

Không còn được xuất hiện lỗi:

```text
Could not configure port: (5, 'Input/output error')
```

Nếu port mở được nhưng không login/không có zone, kiểm tra theo thứ tự:

1. TX/RX đã chéo đúng chưa.
2. GND có chung đúng qua level shifter/cách ly chưa.
3. Mức điện áp chiều SP4000 TX -> Orange Pi RX đã là 3,3 V chưa.
4. `PASSWORD` có đúng PC Communication Password của BabyWare không.
5. SP4000 có thiết bị/phần mềm khác đang chiếm cổng serial không.

## 9. Kiểm tra API và dữ liệu zone

```bash
curl http://127.0.0.1:8082/api/v1/panel
curl http://127.0.0.1:8082/api/v1/zones
curl http://127.0.0.1:8082/api/v1/health
```

- `/panel`: thông tin tủ, trạng thái chạy và storage.
- `/zones`: map zone hiện tại.
- `/health`: trạng thái kết nối/push/queue.

SafeLink tự snapshot toàn bộ zone mỗi 60 giây và POST tới URL đã cấu hình trong `/etc/paradosp/paradosp.conf`. Live event cũng được đẩy riêng, giữ nguyên form `data` gốc.

## 10. Chẩn đoán nhanh

| Hiện tượng | Nguyên nhân có khả năng cao | Việc làm |
|---|---|---|
| `ttyS5: Input/output error` | UART5 chưa được overlay áp dụng/mapping không phải `ttyS5` | Xem bước 4-6; trên máy này dùng `ttyS0`. |
| `ttyS0` có boot log | Serial console chưa tắt | Xem bước 5, đặt `console=none`, reboot. |
| `stty /dev/ttyS0` không lỗi nhưng PAI không login | Dây, điện áp, GND, password hoặc cổng SP4000 bị chiếm | Làm các mục kiểm tra tại bước 8. |
| API `8082` không vào được | Service không chạy | `sudo systemctl status paradosp` và `sudo journalctl -u paradosp -n 100`. |
| App retry nhưng API vẫn vào được | Serial chưa kết nối được | Đây là hành vi mong muốn; sửa cấu hình qua API/Postman, không cần gỡ service. |

