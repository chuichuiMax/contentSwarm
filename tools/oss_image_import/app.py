"""生产环境批量导图测试窗口。"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from oss_import import (
    BATCH_SIZE,
    DEFAULT_BASE_URL,
    DESIGN_STYLES,
    ImportError,
    ProductionClient,
    category_label,
    child_needs_project_details,
    collect_images,
    group_images_by_folder,
    needs_design_style,
    prepare_webp_files,
    uploadable_categories,
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("生产环境批量导图测试")
        self.geometry("760x560")
        self.minsize(680, 480)
        self.client = ProductionClient(DEFAULT_BASE_URL)
        self.categories: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.events: queue.Queue = queue.Queue()
        self.running = False

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        self.base_url = tk.StringVar(value=DEFAULT_BASE_URL)
        self.account = tk.StringVar()
        self.password = tk.StringVar()
        self.folder = tk.StringVar()
        self.style = tk.StringVar()
        self.status = tk.StringVar(value="先登录，再选择可上传的图库。")

        self._row(frame, 0, "服务器", ttk.Entry(frame, textvariable=self.base_url))
        self._row(frame, 1, "账号", ttk.Entry(frame, textvariable=self.account))
        self._row(frame, 2, "密码", ttk.Entry(frame, textvariable=self.password, show="*"))

        self.category_box = ttk.Combobox(frame, state="readonly")
        self._row(frame, 3, "图库", self.category_box)
        self.category_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_style_state())

        self.style_box = ttk.Combobox(frame, textvariable=self.style, values=DESIGN_STYLES, state="disabled")
        self._row(frame, 4, "设计风格", self.style_box)

        folder_row = ttk.Frame(frame)
        folder_entry = ttk.Entry(folder_row, textvariable=self.folder)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder_row, text="选择文件夹", command=self.choose_folder).pack(side=tk.LEFT, padx=(8, 0))
        self._row(frame, 5, "图片目录", folder_row)

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.login_button = ttk.Button(actions, text="登录并加载图库", command=self.login)
        self.login_button.pack(side=tk.LEFT)
        self.start_button = ttk.Button(actions, text="开始导入", command=self.start)
        self.start_button.pack(side=tk.LEFT, padx=8)
        ttk.Label(actions, textvariable=self.status).pack(side=tk.LEFT, padx=8)

        self.log = tk.Text(frame, height=16, wrap="word")
        self.log.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(7, weight=1)
        self.after(200, self._drain)

    def _row(self, frame: ttk.Frame, row: int, label: str, widget) -> None:
        ttk.Label(frame, text=label, width=10).grid(row=row, column=0, sticky="w", pady=4)
        widget.grid(row=row, column=1, sticky="ew", pady=4)

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择要导入的图片文件夹")
        if selected:
            self.folder.set(selected)
            images, skipped = collect_images(Path(selected))
            self.status.set(f"找到 {len(images)} 张可导入图片，跳过 {len(skipped)} 张。")

    def login(self) -> None:
        if self.running:
            return
        self._run("登录", self._login_work)

    def start(self) -> None:
        if self.running:
            return
        if not self.categories:
            messagebox.showwarning("批量导图", "请先登录并加载图库。")
            return
        category = self._selected_category()
        if category is None:
            messagebox.showwarning("批量导图", "请选择图库。")
            return
        if needs_design_style(category, self.by_id) and not self.style.get().strip():
            messagebox.showwarning("批量导图", "案例图库上传需要选择设计风格。")
            return
        folder = Path(self.folder.get().strip())
        if not folder.is_dir():
            messagebox.showwarning("批量导图", "请选择图片文件夹。")
            return
        self._run("导入", lambda: self._import_work(category, folder))

    def _selected_category(self) -> dict | None:
        index = self.category_box.current()
        if index < 0 or index >= len(self.categories):
            return None
        return self.categories[index]

    def _sync_style_state(self) -> None:
        category = self._selected_category()
        if category and needs_design_style(category, self.by_id):
            self.style_box.configure(state="readonly")
            if category.get("design_style"):
                self.style.set(category["design_style"])
        else:
            self.style_box.configure(state="disabled")
            preset = (category or {}).get("design_style") or ""
            self.style.set(preset)

    def _run(self, label: str, work) -> None:
        self.running = True
        self.login_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set(f"{label}中…")
        threading.Thread(target=self._guard, args=(work,), daemon=True).start()

    def _guard(self, work) -> None:
        try:
            work()
        except ImportError as exc:
            self.events.put(("log", f"失败：{exc}"))
            self.events.put(("status", "失败"))
        except Exception as exc:
            self.events.put(("log", f"失败：{exc}"))
            self.events.put(("status", "失败"))
        finally:
            self.events.put(("idle", None))

    def _login_work(self) -> None:
        self.client = ProductionClient(self.base_url.get().strip() or DEFAULT_BASE_URL)
        user = self.client.login(self.account.get(), self.password.get())
        payload = self.client.list_categories()
        categories = payload.get("categories") or []
        self.by_id = {item.get("id"): item for item in categories if item.get("id")}
        selected = uploadable_categories(payload)
        self.categories = selected
        labels = [category_label(item, self.by_id) for item in selected]
        name = user.get("username") or user.get("uid") or "已登录"
        self.events.put(("categories", labels))
        self.events.put(("log", f"已登录 {name}，可上传图库 {len(selected)} 个。"))
        self.events.put(("status", f"已登录 {name}"))

    def _import_work(self, category: dict, folder: Path) -> None:
        style = self.style.get().strip() or None
        if category.get("parent_id"):
            images, skipped = collect_images(folder)
            for line in skipped:
                self.events.put(("log", line))
            if not images:
                raise ImportError("文件夹里没有可导入的图片")
            if not needs_design_style(category, self.by_id):
                style = (category.get("design_style") or "").strip() or style
            self.events.put(("log", "所选图库已是二级图库，图片直接写入该图库。"))
            stored = self._upload_webp(category["id"], images, style)
            self.events.put(("log", f"完成：OSS 可读 {stored}/{len(images)}。"))
            self.events.put(("status", f"完成：OSS 可读 {stored}/{len(images)}"))
            return

        groups, skipped = group_images_by_folder(folder)
        for line in skipped:
            self.events.put(("log", line))
        if not groups:
            raise ImportError("文件夹里没有可导入的图片")
        if child_needs_project_details(category) and not style:
            raise ImportError("案例图库上传需要选择设计风格")
        total = sum(len(images) for _name, images in groups)
        stored = 0
        self.events.put(("log", f"准备在「{category.get('name')}」下导入 {len(groups)} 个文件夹、{total} 张 WebP。"))
        for name, images in groups:
            child = self.client.find_child(category["id"], name)
            if child is None:
                child = self.client.create_child_gallery(category, name, style)
                self.events.put(("log", f"已新建文件夹：{name}"))
            else:
                self.events.put(("log", f"使用已有文件夹：{name}"))
            if not child.get("id"):
                raise ImportError(f"文件夹「{name}」没有返回图库编号")
            upload_style = style if child_needs_project_details(category) else (child.get("design_style") or "").strip() or None
            stored += self._upload_webp(child["id"], images, upload_style)
            self.events.put(("status", f"已确认 {stored}/{total}"))
        self.events.put(("log", f"完成：OSS 可读 {stored}/{total}。"))
        self.events.put(("status", f"完成：OSS 可读 {stored}/{total}"))

    def _upload_webp(self, category_id: str, images: list[Path], style: str | None) -> int:
        prepared = prepare_webp_files(images)
        batches = [prepared[index : index + BATCH_SIZE] for index in range(0, len(prepared), BATCH_SIZE)]
        uploaded = 0
        stored = 0
        for batch_index, batch in enumerate(batches, start=1):
            result = self.client.upload_batch(batch, category_id, style)
            items = result.get("items") or []
            uploaded += len(items)
            self.events.put(("log", f"第 {batch_index}/{len(batches)} 批已提交 {len(items)} 张 WebP，等待写入 OSS。"))
            stored += self._wait_for_oss(category_id, items)
        if uploaded != len(prepared):
            self.events.put(("log", f"提交 {uploaded} 张，本地准备 {len(prepared)} 张。"))
        return stored

    def _wait_for_oss(self, category_id: str, items: list[dict]) -> int:
        pending = {item["id"]: item.get("name") or item["id"] for item in items if item.get("id")}
        deadline = time.time() + 180
        ready = 0
        while pending and time.time() < deadline:
            found = self.client.find_items(category_id, set(pending))
            finished = []
            for item_id, item in found.items():
                if item.get("storage_status") != "completed":
                    continue
                self.client.read_file_header(item)
                name = pending[item_id]
                self.events.put(("log", f"OSS 可读：{name}"))
                finished.append(item_id)
                ready += 1
            for item_id in finished:
                pending.pop(item_id, None)
            if pending:
                time.sleep(2)
        for name in pending.values():
            self.events.put(("log", f"超时未写入 OSS：{name}"))
        return ready

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log.insert(tk.END, payload + "\n")
                self.log.see(tk.END)
            elif kind == "status":
                self.status.set(payload)
            elif kind == "categories":
                self.category_box.configure(values=payload)
                if payload:
                    self.category_box.current(0)
                self._sync_style_state()
            elif kind == "idle":
                self.running = False
                self.login_button.configure(state="normal")
                self.start_button.configure(state="normal")
        self.after(200, self._drain)


if __name__ == "__main__":
    App().mainloop()
