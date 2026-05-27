"""
每日热门消息聚合工具
支持：百度热搜、GitHub Trending、微博热搜
"""

import tkinter as tk
from tkinter import ttk
import threading
import webbrowser
import requests
import re
import json
from datetime import datetime, date, timedelta


class HotNewsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("每日热门消息聚合")
        self.root.geometry("760x580")
        self.root.resizable(True, True)

        self.colors = {
            "bg": "#1e1e2e",
            "card": "#2a2a3d",
            "accent": "#7c3aed",
            "text": "#e0e0e0",
            "subtext": "#888",
        }
        self.root.configure(bg=self.colors["bg"])
        self._build_ui()

    # ── UI 构建 ──────────────────────────────────────────────

    def _build_ui(self):
        top = tk.Frame(self.root, bg=self.colors["card"], height=50)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(
            top, text="  今日热门",
            font=("Microsoft YaHei", 16, "bold"),
            bg=self.colors["card"], fg=self.colors["text"],
        ).pack(side=tk.LEFT, padx=10)

        self.time_label = tk.Label(
            top, text="",
            font=("Microsoft YaHei", 9),
            bg=self.colors["card"], fg=self.colors["subtext"],
        )
        self.time_label.pack(side=tk.RIGHT, padx=15)

        self.refresh_btn = tk.Button(
            top, text="刷新全部",
            font=("Microsoft YaHei", 9, "bold"),
            bg=self.colors["accent"], fg="white",
            relief=tk.FLAT, padx=12, pady=2, cursor="hand2",
            command=self._refresh_all,
        )
        self.refresh_btn.pack(side=tk.RIGHT, padx=5, pady=8)

        # 标签页样式
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=self.colors["card"], foreground=self.colors["text"],
            padding=[18, 6], font=("Microsoft YaHei", 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", "white")],
        )

        self.notebook = ttk.Notebook(self.root, style="TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        self.tabs = {}
        self.listboxes = {}
        self.data_map = {}

        for name in ("百度热搜", "GitHub Trending", "微博热搜"):
            frame = tk.Frame(self.notebook, bg=self.colors["bg"])
            self.notebook.add(frame, text=f"  {name}  ")
            self.tabs[name] = frame

        for name, frame in self.tabs.items():
            lb_frame = tk.Frame(frame, bg=self.colors["bg"])
            lb_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

            scrollbar = tk.Scrollbar(lb_frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            lb = tk.Listbox(
                lb_frame, font=("Microsoft YaHei", 10),
                bg=self.colors["card"], fg=self.colors["text"],
                selectbackground=self.colors["accent"],
                selectforeground="white",
                relief=tk.FLAT, highlightthickness=0, activestyle="none",
                yscrollcommand=scrollbar.set,
            )
            lb.pack(fill=tk.BOTH, expand=True)
            scrollbar.config(command=lb.yview)
            lb.bind("<Double-1>", lambda e, n=name: self._on_double_click(n))
            self.listboxes[name] = lb

        self.status = tk.Label(
            self.root, text="双击条目可打开链接",
            font=("Microsoft YaHei", 8),
            bg=self.colors["bg"], fg=self.colors["subtext"], anchor=tk.W,
        )
        self.status.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.root.after(300, self._refresh_all)

    # ── 数据获取框架 ─────────────────────────────────────────

    def _refresh_all(self):
        self.refresh_btn.config(state=tk.DISABLED, text="加载中...")
        self.time_label.config(text="正在获取...")
        self.status.config(text="正在拉取数据，请稍候...")

        threads = []
        for name, func in [
            ("百度热搜", self._fetch_baidu),
            ("GitHub Trending", self._fetch_github),
            ("微博热搜", self._fetch_weibo),
        ]:
            t = threading.Thread(target=self._fetch_wrapper, args=(name, func), daemon=True)
            t.start()
            threads.append(t)

        threading.Thread(target=self._on_all_done, args=(threads,), daemon=True).start()

    def _fetch_wrapper(self, name, fetch_fn):
        try:
            items = fetch_fn()
            self.root.after(0, lambda: self._update_tab(name, items))
        except Exception as err:
            err_msg = str(err)
            self.root.after(
                0,
                lambda msg=err_msg: self._update_tab(
                    name, [{"title": f"获取失败: {msg}", "url": ""}]
                ),
            )

    def _on_all_done(self, threads):
        for t in threads:
            t.join()
        self.root.after(0, self._finish_refresh)

    def _finish_refresh(self):
        self.refresh_btn.config(state=tk.NORMAL, text="刷新全部")
        now = datetime.now().strftime("%H:%M:%S")
        self.time_label.config(text=f"更新于 {now}")
        self.status.config(text="双击条目可打开链接")

    # ── 百度热搜 ─────────────────────────────────────────────

    def _fetch_baidu(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        items = []

        # 百度官方 API
        try:
            api_url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            resp = requests.get(api_url, headers=headers, timeout=10)
            data = resp.json()
            cards = data.get("data", {}).get("cards", [])
            for card in cards:
                for group in card.get("content", []):
                    for entry in group.get("content", []):
                        word = entry.get("word", "")
                        url = entry.get("url", "")
                        if word:
                            items.append({"title": word, "url": url})
        except Exception:
            pass

        if not items:
            items = [{"title": "获取失败，请检查网络", "url": ""}]
        return items[:30]

    # ── GitHub Trending ──────────────────────────────────────

    def _fetch_github(self):
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/vnd.github.v3+json",
        }
        items = []

        try:
            since = (date.today() - timedelta(days=7)).isoformat()
            api_url = (
                f"https://api.github.com/search/repositories?"
                f"q=created:>{since}&sort=stars&order=desc&per_page=30"
            )
            resp = requests.get(api_url, headers=headers, timeout=10)
            result = resp.json()
            for repo in result.get("items", []):
                lang = repo.get("language", "")
                desc = (repo.get("description") or "")[:60]
                stars = repo.get("stargazers_count", 0)
                display = repo["full_name"]
                if lang:
                    display += f"  [{lang}]"
                display += f"  ★{stars}"
                if desc:
                    display += f"  - {desc}"
                items.append({
                    "title": display,
                    "url": repo["html_url"],
                })
        except Exception:
            pass

        if not items:
            items = [{"title": "获取失败，请检查网络", "url": ""}]
        return items[:30]

    # ── 微博热搜 ─────────────────────────────────────────────

    def _fetch_weibo(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        items = []

        # 方法1：抓取微博热搜页面
        try:
            resp = requests.get(
                "https://s.weibo.com/top/summary",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                matches = re.findall(
                    r'class="td-02".*?href="(.*?)".*?>(.*?)<',
                    resp.text,
                    re.DOTALL,
                )
                for href, title in matches:
                    title = title.strip()
                    if not title:
                        continue
                    full_url = f"https://s.weibo.com{href}" if href.startswith("/") else href
                    items.append({"title": title, "url": full_url})
        except Exception:
            pass

        # 方法2：头条热榜备用
        if not items:
            try:
                resp = requests.get(
                    "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for entry in data:
                        items.append({
                            "title": entry.get("Title", ""),
                            "url": entry.get("Url", ""),
                        })
            except Exception:
                pass

        if not items:
            items = [{"title": "获取失败，请检查网络", "url": ""}]
        return items[:30]

    # ── 更新 UI ──────────────────────────────────────────────

    def _update_tab(self, name, items):
        lb = self.listboxes[name]
        lb.delete(0, tk.END)
        self.data_map[name] = []
        for i, item in enumerate(items):
            text = f"  {i+1:>2}.  {item['title']}"
            lb.insert(tk.END, text)
            lb.itemconfig(i, bg="#252538" if i % 2 == 0 else self.colors["card"])
            self.data_map[name].append(item)

    def _on_double_click(self, name):
        lb = self.listboxes[name]
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        items = self.data_map.get(name, [])
        if idx < len(items) and items[idx].get("url"):
            webbrowser.open(items[idx]["url"])


if __name__ == "__main__":
    root = tk.Tk()
    app = HotNewsApp(root)
    root.mainloop()
