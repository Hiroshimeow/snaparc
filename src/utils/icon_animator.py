# src/utils/icon_animator.py
import os
import time
import threading
from PIL import Image


class IconAnimator:
    def __init__(self, icon_tray, animation_speed, animation_folder, root=None):
        self.icon_tray = icon_tray
        self.s = animation_speed
        self.ani_image = self.load_images_from_folder(animation_folder)
        self.image_index = 0
        self.stop_animation_event = threading.Event()
        self.animation_thread = None
        self.current_icon: Image.Image | None = None  # Will be set from main app
        self.root = root

    def load_images_from_folder(self, folder):
        images = []
        if not os.path.isdir(folder):
            print(f"Animation folder not found: {folder}")
            return images
        try:
            filenames = sorted(os.listdir(folder))
            for filename in filenames:
                if filename.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif")):
                    try:
                        img_path = os.path.join(folder, filename)
                        with Image.open(img_path) as img:
                            img = img.copy()
                        images.append(img)
                    except Exception as e:
                        print(f"Error loading image {filename}: {e}")
        except Exception as e:
            print(f"Error reading folder {folder}: {e}")
        return images

    def animate_icon(self):
        if not self.ani_image:
            print("No animation images loaded.")
            return
        print("Animation thread started.")
        while not self.stop_animation_event.is_set():
            try:
                if self.image_index >= len(self.ani_image):
                    self.image_index = 0
                if self.icon_tray:
                    self.icon_tray.icon = self.ani_image[self.image_index]
                else:
                    break
                self.image_index = (self.image_index + 1) % len(self.ani_image)
            except Exception as e:
                print(f"Error during animation cycle: {e}")
                break
            time.sleep(self.s)

        if self.icon_tray and self.current_icon:
            self.icon_tray.icon = self.current_icon
        print("Animation thread finished.")
        self.animation_thread = None

    def start_animation(self):
        if not self.ani_image or (
            self.animation_thread and self.animation_thread.is_alive()
        ):
            return
        print("Requesting animation start...")
        self.stop_animation_event.clear()
        self.image_index = 0
        self.animation_thread = threading.Thread(target=self.animate_icon, daemon=True)
        self.animation_thread.start()

    def stop_animation(self):
        if self.animation_thread and self.animation_thread.is_alive():
            print("Requesting animation stop...")
            self.stop_animation_event.set()
        elif self.icon_tray and self.current_icon:
            self.icon_tray.icon = self.current_icon
