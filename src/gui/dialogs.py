# /gui/dialogs.py

import customtkinter

class TemperatureDialog(customtkinter.CTkToplevel):
    """Dialog popup cho phép người dùng chỉnh Temperature."""
    def __init__(self, master, current_temp, callback):
        super().__init__(master)
        self.callback = callback

        self.title("Đặt Nhiệt độ")
        self.geometry("300x150")
        self.transient(master)
        self.lift()
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.value_label = customtkinter.CTkLabel(self, text=f"Nhiệt độ: {current_temp:.2f}")
        self.value_label.grid(row=0, column=0, padx=20, pady=(10, 0), sticky="w")

        self.slider = customtkinter.CTkSlider(self, from_=0.0, to=1.0, command=self.update_label)
        self.slider.set(current_temp)
        self.slider.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        button_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=2, column=0, padx=20, pady=10, sticky="e")

        self.ok_button = customtkinter.CTkButton(button_frame, text="OK", width=70, command=self.apply_and_close)
        self.ok_button.pack(side="left", padx=(0, 10))

        self.cancel_button = customtkinter.CTkButton(button_frame, text="Hủy", width=70, command=self.destroy, fg_color="gray")
        self.cancel_button.pack(side="left")

        self.slider.focus()
        self.bind("<Return>", lambda e: self.apply_and_close())
        self.bind("<Escape>", lambda e: self.destroy())
        self.resizable(False, False)

    def update_label(self, value):
        self.value_label.configure(text=f"Nhiệt độ: {value:.2f}")

    def apply_and_close(self):
        new_temp = self.slider.get()
        if self.callback:
            self.callback(new_temp)
        self.destroy()