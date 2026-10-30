# 🌟 Hướng dẫn sử dụng Oath-Gemini Module

`oath-gemini.py` là một module Python mạnh mẽ, được thiết kế để thay thế hoặc chạy song song với `Gemini.py`. Nó sử dụng cơ chế **OAuth 2.0 (Antigravity)** giúp bạn tận dụng tối đa hạn mức (quota) cực cao từ nhiều tài khoản Google cùng lúc.

---

## 📦 1. Các tính năng nổi bật
- **Xoay vòng tài khoản (Round-robin):** Tự động đổi tài khoản khác nếu tài khoản hiện tại hết hạn mức hoặc lỗi.
- **Tự động Retry:** Nếu một yêu cầu thất bại, module sẽ tự động thử lại với tài khoản tiếp theo cho đến khi thành công.
- **Quản lý Token thông minh:** Tự động Refresh Access Token và lưu vào bộ nhớ đệm (`oath_token_cache.json`) để tối ưu tốc độ.
- **Đa năng:** Hỗ trợ cả Text, Hình ảnh và Lịch sử trò chuyện (History).
- **Tương thích ngược:** Có hàm được thiết kế giống hệt `Gemini.py` để thay thế trong 1 nốt nhạc.

---

## 🚀 2. Cách nhúng vào Project (Thay thế Gemini.py)

Nếu bạn đang dùng `Gemini.py` và muốn chuyển sang dùng OAuth để ổn định hơn, hãy làm theo các bước sau:

### Bước 1: Import module
Thay vì import `Gemini`, hãy import `OathGemini`.

```python
# Từ: from Gemini import Gemini
# Sang:
from oath_gemini import OathGemini
```

### Bước 2: Khởi tạo và sử dụng
Hàm `process_image_oath` được thiết kế để có đầu vào và đầu ra tương thích với hàm cũ.

```python
bot = OathGemini(account_file="antigravity-accounts.json")

# Hàm callback để nhận kết quả
def my_callback(result):
    if result:
        print(f"Nội dung: {result['text']}")
        print(f"Dùng tài khoản: {result['api_index']}")
        print(f"Nguồn: {result['key_mask']}") # Sẽ luôn là 'Oath'
    else:
        print("Yêu cầu thất bại sau khi thử tất cả tài khoản.")

# Gọi hàm (Chạy ngầm trong Thread riêng, không gây đơ UI)
bot.process_image_oath(
    image_path="path/to/image.jpg", 
    callback=my_callback,
    history=None # Có thể truyền history format Google nếu có
)
```

---

## 🛠 3. Cách dùng như một Module độc lập (API Mode)

Nếu bạn viết một script mới và muốn lấy kết quả trực tiếp (Blocking mode), hãy dùng hàm `process_oath`.

### Gọi với Text đơn thuần:
```python
from oath_gemini import OathGemini

bot = OathGemini()
text, email = bot.process_oath("Viết một bài thơ về lập trình")

if text:
    print(f"[{email}]: {text}")
```

### Gọi với Hình ảnh + Prompt:
```python
text, email = bot.process_oath(
    prompt="Trích xuất văn bản trong ảnh này",
    image_path="invoice.png",
    model="gemini-2.0-flash" # Có thể chọn model
)
```

---

## 📂 4. Cấu trúc File yêu cầu

Để module hoạt động, thư mục của bạn nên trông như thế này:
```text
project/
├── main.py                 # File chính của bạn
├── oath-gemini.py          # Module này
├── antigravity-accounts.json # File chứa Refresh Tokens (Bắt buộc)
└── oath_token_cache.json   # Tự sinh ra (Không cần quan tâm)
```

---

## ⚠️ 5. Lưu ý quan trọng

1. **File JSON:** Đảm bảo file `antigravity-accounts.json` của bạn có đầy đủ các trường `email` và `refreshToken`.
2. **Thư viện:** Module yêu cầu thư viện `requests` và `Pillow`. Cài đặt bằng:
   ```bash
   pip install requests Pillow
   ```
3. **Xử lý lỗi:** Nếu tất cả tài khoản trong file JSON đều hết hạn mức hoặc lỗi kết nối, hàm sẽ trả về `None`. Bạn nên kiểm tra giá trị trả về trước khi sử dụng.
4. **Model:** Các model hỗ trợ tốt nhất là `gemini-2.0-flash` và `gemini-2.0-pro-exp`.

---
đây là tên định danh để gọi model:
gemini-3-pro
gemini-3-flash
gemini-2.5-pro
gemini-2.5-flash
gemini-2.5-pro
gemini-3-flash-preview
gemini-3-pro-preview
antigravity-claude-sonnet-4-5
antigravity-claude-sonnet-4-5-thinking
antigravity-claude-opus-4-5-thinking