# /gui/themes.py

import json
import os


def get_default_themes():
    """
    Trả về một dictionary chứa các theme mặc định.
    Hàm này được dùng làm phương án dự phòng khi không thể tải file.
    """
    print("Cảnh báo: Sử dụng theme mặc định được mã hóa cứng.")
    return {
        "Dark Default": {
            "bg": "#242424",
            "my_bubble": "#005C4B",
            "my_text": "white",
            "other_bubble": "#333333",
            "other_text": "white",
            "input_bg": "#1A1A1A",
        },
        "Light Nord": {
            "bg": "#ECEFF4",
            "my_bubble": "#5E81AC",
            "my_text": "white",
            "other_bubble": "#D8DEE9",
            "other_text": "#2E3440",
            "input_bg": "#E5E9F0",
        },
        "Dracula": {
            "bg": "#282a36",
            "my_bubble": "#bd93f9",
            "my_text": "#f8f8f2",
            "other_bubble": "#44475a",
            "other_text": "#f8f8f2",
            "input_bg": "#21222c",
        },
    }


def load_themes():
    """
    Tải các theme màu từ file themes.json ở thư mục gốc.
    Nếu có bất kỳ lỗi nào, sẽ trả về các theme mặc định.
    """
    try:
        # Lấy đường dẫn thư mục chứa file themes.py (gui/)
        gui_dir = os.path.dirname(os.path.abspath(__file__))
        themes_file_path = os.path.join(gui_dir, "themes.json")

        if not os.path.exists(themes_file_path):
            print(f"Lỗi: File themes.json không tìm thấy tại '{themes_file_path}'.")
            return get_default_themes()

        with open(themes_file_path, "r", encoding="utf-8") as f:
            themes = json.load(f)
            if not isinstance(themes, dict) or not themes:
                print("Lỗi: Nội dung file themes.json không hợp lệ hoặc rỗng.")
                return get_default_themes()
            return themes

    except json.JSONDecodeError as e:
        print(f"Lỗi phân tích cú pháp file themes.json: {e}.")
        return get_default_themes()
    except Exception as e:
        print(f"Lỗi không xác định khi tải theme: {e}")
        return get_default_themes()
