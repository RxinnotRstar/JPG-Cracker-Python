#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用图片比特流破坏器（JPG）
  - 自动识别格式
  - 默认选中体积最大区域（非文件头）
  - 1-10 bit 可调
  - 随机散选 / 连续片段 两种模式
  - 内存操作，不落盘
  - 支持保存破坏后的图片到短文件名目录
  - 可选择是否保存无法解码的图片
"""
import io, random, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import os
from datetime import datetime


# ---------- 工具 ----------
def scaled_photo(pil_img, box=600):
    w, h = pil_img.size
    s = min(box / w, box / h, 1)
    return ImageTk.PhotoImage(pil_img.resize((int(w * s), int(h * s)), Image.LANCZOS))


# ---------- 解析器 ----------
class RegionScanner:
    """通用扫描：返回 {name: (offset, length), ...}"""
    def __init__(self, data: bytes):
        self.data = data
        self.regions = {}          # name -> (offset, length)
        self._parse()

    def _parse(self):
        if self.data.startswith(b'\xFF\xD8'):
            self._jpg_segments()
        else:
            raise ValueError("仅支持 JPG")

    # -------- JPG --------
    def _jpg_segments(self):
        d = self.data
        i = 2                          # 跳过 SOI
        while i + 4 <= len(d):
            if d[i] != 0xFF:
                i += 1
                continue
            marker = d[i + 1]
            if marker == 0xD9:         # EOI
                break
            if 0xC0 <= marker <= 0xFE:
                seg_len = int.from_bytes(d[i + 2:i + 4], 'big')
                end = i + 2 + seg_len
                if end > len(d):
                    break
                # 只把“真数据”列出来，文件头 SOI/EOI 不破坏
                name = {0xDB: "DQT-量化表",
                        0xC4: "DHT-霍夫曼表",
                        0xDA: "SOS-扫描数据",
                        0xE0: "APP0", 0xE1: "EXIF", 0xFE: "COM"}.get(marker)
                if name:
                    self.regions[name] = (i + 2, seg_len - 2)   # 去掉长度字段本身
                i = end
                continue
            i += 1

    # -------- 公共接口 --------
    def largest_region(self):
        return max(self.regions.items(), key=lambda x: x[1][1])[0]

    def list_regions(self):
        return list(self.regions.keys())


# ---------- 破坏逻辑 ----------
def damage_region(data: bytearray, off: int, size: int, bits: int, mode: str):
    total_bits = size * 8
    if bits > total_bits:
        raise ValueError("bit 数超过区域总位数")
    if mode == "random":
        idxs = random.sample(range(total_bits), bits)
    else:  # 连续
        start = random.randint(0, total_bits - bits)
        idxs = range(start, start + bits)
    for b in idxs:
        byte_i = off + b // 8
        bit_i = b % 8
        data[byte_i] ^= 1 << bit_i


# ---------- GUI ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title("JPG 比特流破坏器")
        root.geometry("720x650")

        top = ttk.Frame(root)
        top.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        ttk.Button(top, text="打开图片", command=self.open_file).pack(side=tk.LEFT)
        ttk.Button(top, text="执行破坏", command=self.damage).pack(side=tk.LEFT, padx=10)

        ttk.Label(top, text="破坏区域:").pack(side=tk.LEFT)
        self.region_var = tk.StringVar()
        self.region_combo = ttk.Combobox(top, textvariable=self.region_var, state="readonly", width=22)
        self.region_combo.pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="bit 数:").pack(side=tk.LEFT)
        self.bits_var = tk.IntVar(value=3)
        ttk.Spinbox(top, from_=1, to=10, textvariable=self.bits_var, width=5).pack(side=tk.LEFT)

        self.mode_var = tk.StringVar(value="random")
        ttk.Radiobutton(top, text="随机散选", variable=self.mode_var, value="random").pack(side=tk.LEFT)
        ttk.Radiobutton(top, text="连续片段", variable=self.mode_var, value="seq").pack(side=tk.LEFT)

        # 保存选项
        self.save_enabled = tk.BooleanVar(value=False)
        self.filter_undecodable = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="保存图片", variable=self.save_enabled).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(top, text="过滤不可解码", variable=self.filter_undecodable).pack(side=tk.LEFT)

        self.status = tk.StringVar(value="请先打开一张 JPG")
        ttk.Label(top, textvariable=self.status).pack(side=tk.RIGHT, padx=5)

        self.img_label = ttk.Label(root, text="（无图片）", anchor=tk.CENTER)
        self.img_label.pack(expand=True, fill=tk.BOTH)

        self.orig_pil = None
        self.orig_bytes = None
        self.current_tk = None
        self.scanner = None
        self.opened_filename = None

    # ---------- 文件 ----------
    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[("JPEG", "*.jpg;*.jpeg")])
        if not path:
            return
        try:
            self.orig_pil = Image.open(path)
            with open(path, "rb") as f:
                self.orig_bytes = bytearray(f.read())
            self.opened_filename = os.path.basename(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
            return
        self.scanner = RegionScanner(self.orig_bytes)
        usable = self.scanner.list_regions()
        if not usable:
            messagebox.showwarning("提示", "未找到可破坏区域（文件头已跳过）")
            return
        self.region_combo["values"] = usable
        # 默认选中体积最大区域
        self.region_combo.set(self.scanner.largest_region())
        self.show_pil(self.orig_pil)
        self.status.set("原图已加载，请确认区域与参数后执行破坏")

    # ---------- 显示 ----------
    def show_pil(self, pil_img):
        self.current_tk = scaled_photo(pil_img, 580)
        self.img_label.configure(image=self.current_tk, text="")

    # ---------- 破坏 ----------
    def damage(self):
        if not self.orig_bytes:
            self.status.set("请先打开图片")
            return
        region = self.region_var.get()
        if not region:
            self.status.set("请选择破坏区域")
            return
        bits = self.bits_var.get()
        mode = self.mode_var.get()
        off, size = self.scanner.regions[region]
        try:
            damaged = bytearray(self.orig_bytes)
            damage_region(damaged, off, size, bits, mode)
            bad_pil = Image.open(io.BytesIO(damaged))
        except Exception as e:
            self.status.set(f"解码失败（{e}）")
            return

        # 尝试解码，检查是否保存不可解码的图片
        try:
            Image.open(io.BytesIO(damaged))
            is_decodable = True
        except:
            is_decodable = False

        if not is_decodable and self.filter_undecodable.get():
            self.status.set(f"破坏完成，但图片无法解码，已跳过保存")
        else:
            # 保存逻辑
            if self.save_enabled.get():
                if not self.opened_filename:
                    self.opened_filename = "unknown.jpg"
                self._save_files(region, damaged)

        self.show_pil(bad_pil)
        self.status.set(f"{region} 破坏完成（翻转 {bits} bit）")

    # ---------- 保存 ----------
    def _save_files(self, region, damaged_bytes):
        # 创建短文件名目录
        short_dir = self.opened_filename[:8]
        save_dir = os.path.join(os.getcwd(), short_dir)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # 保存原始图片
        orig_save_path = os.path.join(save_dir, f"RAW-{self.opened_filename}")
        with open(orig_save_path, "wb") as f:
            f.write(bytes(self.orig_bytes))

        # 保存破坏后的图片
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        damaged_filename = f"{region}-{self.opened_filename}-{timestamp}.jpg"
        damaged_save_path = os.path.join(save_dir, damaged_filename)
        with open(damaged_save_path, "wb") as f:
            f.write(damaged_bytes)

        print(f"保存完成：{short_dir}/{damaged_filename}")


# ---------------- main ----------------
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()