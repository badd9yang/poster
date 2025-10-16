import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Canvas
from PIL import Image, ImageTk
import threading
import zipfile
import tarfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import multiprocessing


class InteractiveQRPosterGenerator:
    def __init__(self, root):
        self.root = root
        
        # ========== 添加高DPI支持 ==========
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)  # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        except:
            try:
                # 备用方案
                from ctypes import windll
                windll.user32.SetProcessDPIAware()
            except:
                pass
        
        # 设置Tkinter的缩放因子
        self.root.tk.call('tk', 'scaling', 2.0)  # 根据你的屏幕调整，1.5-3.0之间
        # ====================================
        
        self.root.title("海报批量合成工具 - 交互版")
        self.root.geometry("1400x900")
        self.background_image = None
        self.background_photo = None
        self.original_bg_image = None  # 添加：保存原始背景图
        # 数据存储
        self.poster_img = None  # PIL Image 原图二维码
        self.qr_img = None      # PIL Image 原图
        self.poster_path_str = ""
        self.qr_folder_str = ""
        self.output_folder_str = ""
        
        # Canvas 显示相关
            
        self.canvas_scale = 1.0
        self.canvas_offset_x = 0
        self.canvas_offset_y = 0
        
        # 添加以下两行：
        self.auto_fit_enabled = True  # 是否启用自动适配
        self._resize_after_id = None  # 用于延迟调整大小
        # 二维码在海报上的位置和尺寸（基于原图像素坐标）
        self.qr_x = 100
        self.qr_y = 100
        self.qr_w = 200
        self.qr_h = 200
        
        # 拖拽状态
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_mode = None  # 'move', 'resize_br', 'resize_tl', 'resize_tr', 'resize_bl', 'pan'
        
        # 缩放模式
        self.aspect_ratio_locked = tk.BooleanVar(value=True)  # 默认锁定等比例
        self.original_aspect_ratio = 1.0  # 原始宽高比
        
        # 对齐辅助
        self.snap_enabled = tk.BooleanVar(value=True)  # 启用吸附
        self.snap_threshold = 10  # 吸附阈值（像素）
        self.guide_lines = []  # 辅助线坐标
        
        # 撤销/重做功能
        self.history = deque(maxlen=50)  # 最多保存50步历史
        self.redo_stack = deque(maxlen=50)
        
        # 用于防止递归更新
        self.updating_from_code = False
        # 添加文件命名相关变量
        self.naming_pattern = tk.StringVar(value="{original}")  # 默认使用原文件名
        self.naming_start_number = tk.IntVar(value=1)  # 序号起始值
        self.naming_prefix = tk.StringVar(value="")  # 前缀
        self.naming_suffix = tk.StringVar(value="")  # 后缀
        
        self.setup_ui()
        self.setup_shortcuts()
        self.save_state()  # 保存初始状态
        
    def setup_ui(self):
        # 左侧控制面板 - 使用Canvas+Scrollbar实现滚动
        left_container = ttk.Frame(self.root, width=340)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        left_container.pack_propagate(False)
        
        # 创建Canvas和Scrollbar
        left_canvas = Canvas(left_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
        
        # 创建可滚动的Frame
        self.scrollable_frame = ttk.Frame(left_canvas)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        )
        
        left_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        left_canvas.configure(yscrollcommand=scrollbar.set)
        
        # 绑定鼠标滚轮到左侧面板
        def _on_mousewheel(event):
            left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def _on_mousewheel_linux(event):
            if event.num == 4:
                left_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                left_canvas.yview_scroll(1, "units")
        
        left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        left_canvas.bind_all("<Button-4>", _on_mousewheel_linux)
        left_canvas.bind_all("<Button-5>", _on_mousewheel_linux)
        
        scrollbar.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)
        
        # 使用scrollable_frame作为左侧面板
        left_panel = self.scrollable_frame
        
        # 标题
        title_label = ttk.Label(left_panel, text="交互式海报合成", font=("Arial", 14, "bold"))
        title_label.pack(pady=(0, 20))
        
        # 海报选择
        ttk.Label(left_panel, text="1. 选择海报图片", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 5), padx=10)
        poster_frame = ttk.Frame(left_panel)
        poster_frame.pack(fill=tk.X, pady=5, padx=10)
        self.poster_path_label = ttk.Label(poster_frame, text="未选择", foreground="gray", wraplength=200)
        self.poster_path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(poster_frame, text="浏览", command=self.select_poster, width=8).pack(side=tk.RIGHT)
        
        # 二维码文件夹选择
        ttk.Label(left_panel, text="2. 选择批量替换图片的文件夹", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(20, 5), padx=10)
        qr_frame = ttk.Frame(left_panel)
        qr_frame.pack(fill=tk.X, pady=5, padx=10)
        self.qr_folder_label = ttk.Label(qr_frame, text="未选择", foreground="gray", wraplength=200)
        self.qr_folder_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(qr_frame, text="浏览", command=self.select_qr_folder, width=8).pack(side=tk.RIGHT)
        
        # 输出文件夹选择
        ttk.Label(left_panel, text="3. 选择输出文件夹", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(20, 5), padx=10)
        output_frame = ttk.Frame(left_panel)
        output_frame.pack(fill=tk.X, pady=5, padx=10)
        self.output_folder_label = ttk.Label(output_frame, text="未选择", foreground="gray", wraplength=200)
        self.output_folder_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(output_frame, text="浏览", command=self.select_output_folder, width=8).pack(side=tk.RIGHT)
        
        # 缩放模式选择
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20, padx=10)
        ttk.Label(left_panel, text="缩放模式", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5), padx=10)
        
        mode_frame = ttk.Frame(left_panel)
        mode_frame.pack(fill=tk.X, padx=10)
        
        ttk.Checkbutton(mode_frame, text="🔒 锁定等比例缩放", variable=self.aspect_ratio_locked,
                       command=self.on_aspect_ratio_toggle).pack(anchor=tk.W)
        
        ttk.Checkbutton(mode_frame, text="🧲 启用智能对齐", variable=self.snap_enabled).pack(anchor=tk.W, pady=(5, 0))
        
        # 位置和尺寸输入区域
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20, padx=10)
        ttk.Label(left_panel, text="被替换图片的位置与尺寸", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 10), padx=10)
        
        # 创建输入框
        input_frame = ttk.Frame(left_panel)
        input_frame.pack(fill=tk.X, padx=15)
        
        # X坐标
        x_frame = ttk.Frame(input_frame)
        x_frame.pack(fill=tk.X, pady=3)
        ttk.Label(x_frame, text="X坐标:", width=8).pack(side=tk.LEFT)
        self.x_var = tk.StringVar(value="100")
        x_entry = ttk.Entry(x_frame, textvariable=self.x_var, width=10)
        x_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(x_frame, text="px").pack(side=tk.LEFT, padx=(3, 0))
        x_entry.bind("<Return>", lambda e: self.apply_manual_input())
        
        # Y坐标
        y_frame = ttk.Frame(input_frame)
        y_frame.pack(fill=tk.X, pady=3)
        ttk.Label(y_frame, text="Y坐标:", width=8).pack(side=tk.LEFT)
        self.y_var = tk.StringVar(value="100")
        y_entry = ttk.Entry(y_frame, textvariable=self.y_var, width=10)
        y_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(y_frame, text="px").pack(side=tk.LEFT, padx=(3, 0))
        y_entry.bind("<Return>", lambda e: self.apply_manual_input())
        
        # 宽度
        w_frame = ttk.Frame(input_frame)
        w_frame.pack(fill=tk.X, pady=3)
        ttk.Label(w_frame, text="宽度:", width=8).pack(side=tk.LEFT)
        self.w_var = tk.StringVar(value="200")
        w_entry = ttk.Entry(w_frame, textvariable=self.w_var, width=10)
        w_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(w_frame, text="px").pack(side=tk.LEFT, padx=(3, 0))
        w_entry.bind("<Return>", lambda e: self.apply_manual_input())
        w_entry.bind("<KeyRelease>", lambda e: self.on_width_change())
        
        # 高度
        h_frame = ttk.Frame(input_frame)
        h_frame.pack(fill=tk.X, pady=3)
        ttk.Label(h_frame, text="高度:", width=8).pack(side=tk.LEFT)
        self.h_var = tk.StringVar(value="200")
        h_entry = ttk.Entry(h_frame, textvariable=self.h_var, width=10)
        h_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(h_frame, text="px").pack(side=tk.LEFT, padx=(3, 0))
        h_entry.bind("<Return>", lambda e: self.apply_manual_input())
        h_entry.bind("<KeyRelease>", lambda e: self.on_height_change())
        
        # 应用按钮
        apply_btn = ttk.Button(input_frame, text="应用数值", command=self.apply_manual_input)
        apply_btn.pack(fill=tk.X, pady=(10, 0))
        
        # 快捷设置按钮
        quick_frame = ttk.Frame(input_frame)
        quick_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(quick_frame, text="居中", command=self.center_qr, width=10).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(quick_frame, text="原始尺寸", command=self.reset_to_original_size, width=10).pack(side=tk.LEFT)
        
        # 当前信息显示
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15, padx=10)
        
        info_label = ttk.Label(left_panel, text="当前状态", font=("Arial", 9, "bold"))
        info_label.pack(anchor=tk.W, pady=(0, 5), padx=10)
        
        self.info_text = tk.Text(left_panel, height=4, width=30, state=tk.DISABLED, 
                                 font=("Courier", 8), bg="#f0f0f0", relief=tk.FLAT)
        self.info_text.pack(fill=tk.X, padx=15)
        
        # 操作提示
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15, padx=10)
        tips_label = ttk.Label(left_panel, text="操作提示:", font=("Arial", 9, "bold"))
        tips_label.pack(anchor=tk.W, padx=10)
        
        tips = [
            "• 滚轮+Ctrl：缩放视图",
            "• 滚轮+Shift：拖动背景",
            "• 拖动被替换的图片：移动位置",
            "• 拖动四角：调整尺寸",
            "• 输入框：精确设置",
            "• Ctrl+Z/Y：撤销/重做"
        ]
        for tip in tips:
            ttk.Label(left_panel, text=tip, font=("Arial", 8), foreground="#555").pack(anchor=tk.W, pady=1, padx=10)
        
        
        # 输出质量选项
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15, padx=10)
        ttk.Label(left_panel, text="输出质量", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5), padx=10)


        quality_frame = ttk.Frame(left_panel)
        quality_frame.pack(fill=tk.X, padx=10)

        # 格式选择
        format_frame = ttk.Frame(quality_frame)
        format_frame.pack(fill=tk.X, pady=(0, 10))

        self.output_format = tk.StringVar(value="png")
        ttk.Radiobutton(format_frame, text="PNG (无损)", variable=self.output_format, 
                        value="png", command=self.on_format_change).pack(anchor=tk.W)
        ttk.Radiobutton(format_frame, text="JPEG (有损压缩)", variable=self.output_format, 
                        value="jpeg", command=self.on_format_change).pack(anchor=tk.W)

        # JPEG质量设置区域
        self.jpeg_quality_frame = ttk.Frame(quality_frame)
        self.jpeg_quality_frame.pack(fill=tk.X, pady=(5, 0))

        quality_input_frame = ttk.Frame(self.jpeg_quality_frame)
        quality_input_frame.pack(fill=tk.X)

        ttk.Label(quality_input_frame, text="JPEG质量:").pack(side=tk.LEFT)
        self.quality_var = tk.StringVar(value="95")
        quality_spinbox = ttk.Spinbox(quality_input_frame, from_=1, to=100, 
                                    textvariable=self.quality_var, width=8,
                                    command=self.on_quality_change)
        quality_spinbox.pack(side=tk.LEFT, padx=(5, 3))
        ttk.Label(quality_input_frame, text="(1-100)").pack(side=tk.LEFT)

        # 质量提示标签
        self.quality_hint = ttk.Label(self.jpeg_quality_frame, 
                                    text="95 = 高质量 (推荐)", 
                                    font=("Arial", 8), foreground="#666")
        self.quality_hint.pack(anchor=tk.W, pady=(2, 0))

        # 质量滑块
        self.quality_scale = ttk.Scale(self.jpeg_quality_frame, from_=1, to=100, 
                                    orient=tk.HORIZONTAL, command=self.on_scale_change)
        self.quality_scale.set(95)
        self.quality_scale.pack(fill=tk.X, pady=(5, 0))

        # 预设按钮
        preset_frame = ttk.Frame(self.jpeg_quality_frame)
        preset_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(preset_frame, text="高(95)", command=lambda: self.set_quality(95), 
                width=8).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(preset_frame, text="中(80)", command=lambda: self.set_quality(80), 
                width=8).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(preset_frame, text="低(60)", command=lambda: self.set_quality(60), 
                width=8).pack(side=tk.LEFT)

        # 初始状态：隐藏JPEG质量设置
        self.jpeg_quality_frame.pack_forget()

        
        # 在输出质量选项后添加
        # 文件命名选项
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15, padx=10)
        ttk.Label(left_panel, text="输出文件命名", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5), padx=10)

        naming_frame = ttk.Frame(left_panel)
        naming_frame.pack(fill=tk.X, padx=10)

        # 命名模式选择
        ttk.Label(naming_frame, text="命名方式:", font=("Arial", 9)).pack(anchor=tk.W, pady=(0, 5))

        naming_options = [
            ("{original}", "保持被替换文件名"),
            ("{number}", "纯数字序号 (1, 2, 3...)"),
            ("{number:04d}", "补零序号 (0001, 0002...)"),
            ("{prefix}{original}", "前缀+原名"),
            ("{original}{suffix}", "原名+后缀"),
            ("{prefix}{number:04d}", "前缀+序号"),
            ("custom", "自定义模板")
        ]

        for pattern, label in naming_options:
            ttk.Radiobutton(naming_frame, text=label, variable=self.naming_pattern, 
                        value=pattern, command=self.on_naming_change).pack(anchor=tk.W, pady=2)

        # 自定义选项输入框
        self.custom_naming_frame = ttk.Frame(naming_frame)
        self.custom_naming_frame.pack(fill=tk.X, pady=(5, 0))

        # 前缀输入
        prefix_frame = ttk.Frame(self.custom_naming_frame)
        prefix_frame.pack(fill=tk.X, pady=2)
        ttk.Label(prefix_frame, text="前缀:", width=8).pack(side=tk.LEFT)
        prefix_entry = ttk.Entry(prefix_frame, textvariable=self.naming_prefix, width=15)
        prefix_entry.pack(side=tk.LEFT, padx=(5, 0))
        # ========== 添加实时更新 ==========
        prefix_entry.bind("<KeyRelease>", lambda e: self.update_naming_preview())
        # =================================

        # 后缀输入
        suffix_frame = ttk.Frame(self.custom_naming_frame)
        suffix_frame.pack(fill=tk.X, pady=2)
        ttk.Label(suffix_frame, text="后缀:", width=8).pack(side=tk.LEFT)
        suffix_entry = ttk.Entry(suffix_frame, textvariable=self.naming_suffix, width=15)
        suffix_entry.pack(side=tk.LEFT, padx=(5, 0))
        # ========== 添加实时更新 ==========
        suffix_entry.bind("<KeyRelease>", lambda e: self.update_naming_preview())
        # =================================

        # 序号起始值
        number_frame = ttk.Frame(self.custom_naming_frame)
        number_frame.pack(fill=tk.X, pady=2)
        ttk.Label(number_frame, text="起始序号:", width=8).pack(side=tk.LEFT)
        ttk.Spinbox(number_frame, from_=0, to=9999, textvariable=self.naming_start_number, 
                width=8, command=self.update_naming_preview).pack(side=tk.LEFT, padx=(5, 0))
        # ========== 注意：Spinbox用command参数 ==========

        # 自定义模板输入
        self.custom_template_frame = ttk.Frame(naming_frame)
        ttk.Label(self.custom_template_frame, text="自定义模板:", font=("Arial", 8)).pack(anchor=tk.W, pady=(5, 2))
        self.custom_template_var = tk.StringVar(value="{original}")
        template_entry = ttk.Entry(self.custom_template_frame, textvariable=self.custom_template_var, width=25)
        template_entry.pack(fill=tk.X)
        # ========== 添加实时更新 ==========
        template_entry.bind("<KeyRelease>", lambda e: self.update_naming_preview())
        # =================================
        
        # 模板说明
        template_help = ttk.Label(self.custom_template_frame, 
                                text="可用变量:\n{original} - 原文件名\n{number} - 序号\n{prefix} - 前缀\n{suffix} - 后缀\n{date} - 日期(YYYYMMDD)\n{time} - 时间(HHMMSS)",
                                font=("Arial", 7), foreground="#666", justify=tk.LEFT)
        template_help.pack(anchor=tk.W, pady=(2, 0))

        # 预览（修改为支持多行显示）
        self.naming_preview_label = ttk.Label(
            naming_frame, 
            text="预览: 请先选择替换图片文件夹", 
            font=("Courier", 8),  # 使用等宽字体
            foreground="gray",
            justify=tk.LEFT,      # 左对齐
            wraplength=280        # 自动换行
        )
        self.naming_preview_label.pack(anchor=tk.W, pady=(10, 0))


        # 初始隐藏自定义选项
        self.custom_naming_frame.pack_forget()
        self.custom_template_frame.pack_forget()
        
        
        # 底部按钮区域
        ttk.Separator(left_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20, padx=10)
        
        button_frame = ttk.Frame(left_panel)
        button_frame.pack(fill=tk.X, pady=(0, 20), padx=10)
        
        self.process_btn = ttk.Button(button_frame, text="🚀 开始批量合成", command=self.start_processing, state=tk.DISABLED)
        self.process_btn.pack(fill=tk.X, pady=5)
        
        self.status_label = ttk.Label(button_frame, text="就绪", foreground="blue", font=("Arial", 9))
        self.status_label.pack(pady=5)
        
        self.progress = ttk.Progressbar(button_frame, mode='determinate')
        self.progress.pack(fill=tk.X, pady=5)
        
        # 右侧画布区域
        right_panel = ttk.Frame(self.root)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)
        
        canvas_label = ttk.Label(right_panel, text="预览区域", font=("Arial", 11, "bold"))
        canvas_label.pack(pady=(0, 10))
        
        # 创建Canvas
        self.canvas = Canvas(right_panel, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # 添加这一行：绑定Canvas大小变化事件
        self.canvas.bind('<Configure>', self.on_canvas_resize)
        # 绑定事件
        self.canvas.bind("<Control-MouseWheel>", self.on_canvas_mousewheel)
        self.canvas.bind("<Control-Button-4>", self.on_canvas_mousewheel)
        self.canvas.bind("<Control-Button-5>", self.on_canvas_mousewheel)
        
        self.canvas.bind("<Shift-MouseWheel>", self.on_pan_mousewheel)
        self.canvas.bind("<Shift-Button-4>", self.on_pan_mousewheel)
        self.canvas.bind("<Shift-Button-5>", self.on_pan_mousewheel)
        
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        
        # 中键拖拽背景
        self.canvas.bind("<ButtonPress-2>", self.on_pan_press)
        self.canvas.bind("<B2-Motion>", self.on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self.on_pan_release)
        

    def on_naming_change(self):
        """命名模式改变时显示/隐藏相关选项"""
        pattern = self.naming_pattern.get()
        
        # 隐藏所有自定义选项
        self.custom_naming_frame.pack_forget()
        self.custom_template_frame.pack_forget()
        
        # 根据选择显示对应选项
        if pattern in ["{prefix}{original}", "{original}{suffix}", "{prefix}{number:04d}"]:
            self.custom_naming_frame.pack(fill=tk.X, pady=(5, 0))
        elif pattern == "custom":
            self.custom_template_frame.pack(fill=tk.X, pady=(5, 0))
        
        # ========== 修改这里：立即更新预览 ==========
        self.update_naming_preview()
        # ==========================================


    def update_naming_preview(self):
        """更新文件名预览 - 使用真实文件"""
        try:
            # 检查是否已选择二维码文件夹
            if not self.qr_folder_str or not os.path.exists(self.qr_folder_str):
                self.naming_preview_label.configure(
                    text="预览: 请先选择替换图片文件夹", 
                    foreground="gray"
                )
                return
            
            # 获取文件夹中的图片文件
            qr_files = [f for f in os.listdir(self.qr_folder_str) 
                        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            
            if not qr_files:
                self.naming_preview_label.configure(
                    text="预览: 文件夹中没有图片", 
                    foreground="orange"
                )
                return
            
            # 使用第一个真实文件生成预览
            first_file = qr_files[0]
            sample_name_1 = self.generate_filename(first_file, 0)
            
            # 如果有多个文件，显示前两个
            if len(qr_files) > 1:
                second_file = qr_files[1]
                sample_name_2 = self.generate_filename(second_file, 1)
                preview_text = f"预览:\n  {sample_name_1}\n  {sample_name_2}"
                if len(qr_files) > 2:
                    preview_text += f"\n  ... 共{len(qr_files)}个文件"
            else:
                preview_text = f"预览: {sample_name_1}"
            
            self.naming_preview_label.configure(
                text=preview_text, 
                foreground="blue"
            )
            
        except Exception as e:
            self.naming_preview_label.configure(
                text=f"预览错误: {str(e)}", 
                foreground="red"
            )


    def generate_filename(self, original_filename, index):
        """
        根据命名模式生成输出文件名
        
        Args:
            original_filename: 原始文件名（含扩展名）
            index: 当前文件索引（从0开始）
        
        Returns:
            生成的文件名（含扩展名）
        """
        from datetime import datetime
        
        # 分离文件名和扩展名
        base_name = os.path.splitext(original_filename)[0]
        
        # 确定输出扩展名
        output_format = self.output_format.get()
        extension = ".png" if output_format == "png" else ".jpg"
        
        # 获取命名模式
        pattern = self.naming_pattern.get()
        
        # 准备变量
        number = self.naming_start_number.get() + index
        prefix = self.naming_prefix.get()
        suffix = self.naming_suffix.get()
        date_str = datetime.now().strftime("%Y%m%d")
        time_str = datetime.now().strftime("%H%M%S")
        
        # 根据模式生成文件名
        if pattern == "{original}":
            filename = base_name
        
        elif pattern == "{number}":
            filename = str(number)
        
        elif pattern == "{number:04d}":
            filename = f"{number:04d}"
        
        elif pattern == "{prefix}{original}":
            filename = f"{prefix}{base_name}"
        
        elif pattern == "{original}{suffix}":
            filename = f"{base_name}{suffix}"
        
        elif pattern == "{prefix}{number:04d}":
            filename = f"{prefix}{number:04d}"
        
        elif pattern == "custom":
            # 自定义模板
            template = self.custom_template_var.get()
            filename = template.format(
                original=base_name,
                number=number,
                prefix=prefix,
                suffix=suffix,
                date=date_str,
                time=time_str
            )
            # 处理数字格式化（如 {number:04d}）
            import re
            filename = re.sub(r'\{number:(\d+)d\}', lambda m: f"{number:0{m.group(1)}d}", filename)
        
        else:
            filename = base_name
        
        # 清理非法字符
        illegal_chars = '<>:"/\\|?*'
        for char in illegal_chars:
            filename = filename.replace(char, '_')
        
        return filename + extension

    def on_format_change(self):
        """格式改变时显示/隐藏JPEG质量设置"""
        if self.output_format.get() == "jpeg":
            self.jpeg_quality_frame.pack(fill=tk.X, pady=(5, 0))
        else:
            self.jpeg_quality_frame.pack_forget()
        
        # ========== 添加这一行 ==========
        self.update_naming_preview()  # 扩展名会变化
        # ================================

    def on_quality_change(self):
        """质量输入框改变时更新滑块和提示"""
        try:
            value = int(self.quality_var.get())
            value = max(1, min(100, value))  # 限制范围
            self.quality_scale.set(value)
            self.update_quality_hint(value)
        except ValueError:
            pass

    def on_scale_change(self, value):
        """滑块改变时更新输入框和提示"""
        value = int(float(value))
        self.quality_var.set(str(value))
        self.update_quality_hint(value)

    def update_quality_hint(self, value):
        """更新质量提示文字"""
        if value >= 90:
            hint = f"{value} = 高质量 (文件较大)"
            color = "#2e7d32"
        elif value >= 75:
            hint = f"{value} = 中等质量 (推荐)"
            color = "#1976d2"
        elif value >= 50:
            hint = f"{value} = 低质量 (文件较小)"
            color = "#f57c00"
        else:
            hint = f"{value} = 极低质量 (可能失真)"
            color = "#d32f2f"
        
        self.quality_hint.configure(text=hint, foreground=color)

    def set_quality(self, value):
        """设置预设质量值"""
        self.quality_var.set(str(value))
        self.quality_scale.set(value)
        self.update_quality_hint(value)

    def setup_shortcuts(self):
        """设置快捷键"""
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-Z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Control-Y>", lambda e: self.redo())
    
    def on_aspect_ratio_toggle(self):
        """切换等比例缩放模式"""
        if self.aspect_ratio_locked.get() and self.qr_img:
            self.original_aspect_ratio = self.qr_w / self.qr_h
            self.status_label.configure(text="已锁定等比例", foreground="green")
        else:
            self.status_label.configure(text="自由缩放模式", foreground="blue")
    
    def on_width_change(self):
        """宽度改变时，如果锁定等比例则自动调整高度"""
        if self.aspect_ratio_locked.get() and not self.updating_from_code:
            try:
                new_w = float(self.w_var.get())
                if new_w > 0:
                    new_h = new_w / self.original_aspect_ratio
                    self.updating_from_code = True
                    self.h_var.set(str(int(new_h)))
                    self.updating_from_code = False
            except ValueError:
                pass
    
    def on_height_change(self):
        """高度改变时，如果锁定等比例则自动调整宽度"""
        if self.aspect_ratio_locked.get() and not self.updating_from_code:
            try:
                new_h = float(self.h_var.get())
                if new_h > 0:
                    new_w = new_h * self.original_aspect_ratio
                    self.updating_from_code = True
                    self.w_var.set(str(int(new_w)))
                    self.updating_from_code = False
            except ValueError:
                pass
    
    def reset_to_original_size(self):
        """重置为二维码原始尺寸"""
        if self.qr_img:
            self.qr_w = self.qr_img.width
            self.qr_h = self.qr_img.height
            self.original_aspect_ratio = self.qr_w / self.qr_h
            self.update_input_fields()
            self.redraw_canvas()
            self.save_state()
            self.status_label.configure(text="已恢复原始尺寸", foreground="green")
    
    def save_state(self):
        """保存当前状态到历史记录"""
        state = {
            'qr_x': float(self.qr_x),
            'qr_y': float(self.qr_y),
            'qr_w': float(self.qr_w),
            'qr_h': float(self.qr_h)
        }
        self.history.append(state)
        # 新操作会清空重做栈
        self.redo_stack.clear()
    
    def undo(self):
        """撤销操作"""
        if len(self.history) > 1:  # 至少保留一个状态
            # 将当前状态放入重做栈
            current_state = self.history.pop()
            self.redo_stack.append(current_state)
            
            # 恢复上一个状态
            prev_state = self.history[-1]
            self.qr_x = prev_state['qr_x']
            self.qr_y = prev_state['qr_y']
            self.qr_w = prev_state['qr_w']
            self.qr_h = prev_state['qr_h']
            
            self.update_input_fields()
            self.redraw_canvas()
            self.status_label.configure(text="已撤销", foreground="blue")
        else:
            self.status_label.configure(text="无法撤销", foreground="gray")
    
    def redo(self):
        """重做操作"""
        if self.redo_stack:
            # 从重做栈取出状态
            state = self.redo_stack.pop()
            self.history.append(state)
            
            # 恢复状态
            self.qr_x = state['qr_x']
            self.qr_y = state['qr_y']
            self.qr_w = state['qr_w']
            self.qr_h = state['qr_h']
            
            self.update_input_fields()
            self.redraw_canvas()
            self.status_label.configure(text="已重做", foreground="blue")
        else:
            self.status_label.configure(text="无法重做", foreground="gray")
    
    def update_input_fields(self):
        """更新输入框的值"""
        if not self.updating_from_code:
            self.updating_from_code = True
            self.x_var.set(str(int(self.qr_x)))
            self.y_var.set(str(int(self.qr_y)))
            self.w_var.set(str(int(self.qr_w)))
            self.h_var.set(str(int(self.qr_h)))
            self.updating_from_code = False
    
    def apply_manual_input(self):
        """应用手动输入的数值"""
        try:
            new_x = float(self.x_var.get())
            new_y = float(self.y_var.get())
            new_w = float(self.w_var.get())
            new_h = float(self.h_var.get())
            
            # 验证数值
            if new_w <= 0 or new_h <= 0:
                messagebox.showwarning("警告", "宽度和高度必须大于0")
                return
            
            if self.poster_img:
                # 限制在海报范围内
                new_x = max(0, min(new_x, self.poster_img.width - new_w))
                new_y = max(0, min(new_y, self.poster_img.height - new_h))
            
            self.qr_x = new_x
            self.qr_y = new_y
            self.qr_w = new_w
            self.qr_h = new_h
            
            # 更新宽高比
            if self.aspect_ratio_locked.get():
                self.original_aspect_ratio = self.qr_w / self.qr_h
            
            self.update_input_fields()
            self.redraw_canvas()
            self.save_state()
            self.status_label.configure(text="已应用数值", foreground="green")
            
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
    
    def center_qr(self):
        """将二维码居中"""
        if self.poster_img:
            self.qr_x = (self.poster_img.width - self.qr_w) / 2
            self.qr_y = (self.poster_img.height - self.qr_h) / 2
            self.update_input_fields()
            self.redraw_canvas()
            self.save_state()
            self.status_label.configure(text="已居中", foreground="green")
    
    def select_poster(self):
        file_path = filedialog.askopenfilename(
            title="选择海报图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif")]
        )
        if file_path:
            try:
                self.poster_img = Image.open(file_path).convert("RGBA")
                self.poster_path_str = file_path
                self.poster_path_label.configure(text=os.path.basename(file_path), foreground="black")
                
                # 初始化二维码位置为海报中心附近
                self.qr_x = self.poster_img.width // 2 - 100
                self.qr_y = self.poster_img.height // 2 - 100
                
                # 重置缩放并启用自动适配
                self.canvas_scale = 1.0
                self.auto_fit_enabled = True  # 添加这行
                
                self.update_input_fields()
                self.redraw_canvas()
                self.check_ready()
                self.save_state()
            except Exception as e:
                messagebox.showerror("错误", f"无法加载海报: {e}")

    
    def select_qr_folder(self):
        folder_path = filedialog.askdirectory(title="选择被替换的图片文件夹")
        if folder_path:
            qr_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            if not qr_files:
                messagebox.showwarning("警告", "文件夹中没有找到图片文件")
                return
            
            try:
                # 加载第一个二维码作为预览
                first_qr = os.path.join(folder_path, qr_files[0])
                self.qr_img = Image.open(first_qr).convert("RGBA")
                self.qr_folder_str = folder_path
                self.qr_folder_label.configure(text=f"{os.path.basename(folder_path)} ({len(qr_files)}张)", foreground="black")
                
                # 初始化二维码尺寸
                self.qr_w = min(self.qr_img.width, 300)
                self.qr_h = min(self.qr_img.height, 300)
                self.original_aspect_ratio = self.qr_img.width / self.qr_img.height
                
                self.update_input_fields()
                self.redraw_canvas()
                self.check_ready()
                self.save_state()
                # ========== 添加这一行 ==========
                self.update_naming_preview()  # 更新预览
                # ================================
            except Exception as e:
                messagebox.showerror("错误", f"无法加载被替换的图片: {e}")

    def select_output_folder(self):
        folder_path = filedialog.askdirectory(title="选择输出文件夹")
        if folder_path:
            self.output_folder_str = folder_path
            self.output_folder_label.configure(text=os.path.basename(folder_path), foreground="black")
            self.check_ready()
    
    def check_ready(self):
        if self.poster_img and self.qr_img and self.output_folder_str:
            self.process_btn.configure(state=tk.NORMAL)
        else:
            self.process_btn.configure(state=tk.DISABLED)
    
    def calculate_snap_position(self, value, target, threshold):
        """计算吸附位置"""
        if abs(value - target) < threshold:
            return target, True
        return value, False
    def on_canvas_resize(self, event):
        """Canvas大小改变时的回调"""
        if not self.poster_img:
            return
        
        # 取消之前的延迟调用，避免频繁重绘
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        
        # 延迟100ms后执行，避免窗口调整过程中频繁重绘
        self._resize_after_id = self.root.after(100, self.recalculate_and_redraw)

    def recalculate_and_redraw(self):
        """重新计算缩放比例并重绘"""
        if not self.poster_img or not self.auto_fit_enabled:
            return
        
        # 获取Canvas当前尺寸
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        if canvas_w <= 1 or canvas_h <= 1:
            return
        
        # 重新计算适配缩放比例
        scale_w = (canvas_w - 40) / self.poster_img.width
        scale_h = (canvas_h - 40) / self.poster_img.height
        self.canvas_scale = min(scale_w, scale_h, 1.0)
        
        # 重新计算居中偏移
        self.canvas_offset_x = (canvas_w - self.poster_img.width * self.canvas_scale) / 2
        self.canvas_offset_y = (canvas_h - self.poster_img.height * self.canvas_scale) / 2
        
        # 重绘
        self.redraw_canvas()
    
    def redraw_canvas(self):
        self.canvas.delete("all")
        self.guide_lines = []
        
        if not self.poster_img:
            return
        
        # 获取Canvas尺寸
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        if canvas_w <= 1 or canvas_h <= 1:
            self.root.after(100, self.redraw_canvas)
            return

        # 计算适配缩放比例（首次加载时或自动适配模式）
        if self.canvas_scale == 1.0 and self.auto_fit_enabled:
            scale_w = (canvas_w - 40) / self.poster_img.width
            scale_h = (canvas_h - 40) / self.poster_img.height
            self.canvas_scale = min(scale_w, scale_h, 1.0)  # 不超过原图大小
            self.canvas_offset_x = (canvas_w - self.poster_img.width * self.canvas_scale) / 2
            self.canvas_offset_y = (canvas_h - self.poster_img.height * self.canvas_scale) / 2


        
        # 绘制海报
        display_w = int(self.poster_img.width * self.canvas_scale)
        display_h = int(self.poster_img.height * self.canvas_scale)
        
        if display_w > 0 and display_h > 0:
            poster_resized = self.poster_img.resize((display_w, display_h), Image.Resampling.LANCZOS)
            self.poster_photo = ImageTk.PhotoImage(poster_resized)
            self.canvas.create_image(self.canvas_offset_x, self.canvas_offset_y, 
                                    image=self.poster_photo, anchor=tk.NW, tags="poster")
        
        # 绘制辅助线（中线）
        if self.snap_enabled.get() and self.poster_img:
            poster_center_x = self.canvas_offset_x + (self.poster_img.width / 2) * self.canvas_scale
            poster_center_y = self.canvas_offset_y + (self.poster_img.height / 2) * self.canvas_scale
            
            # 垂直中线
            self.canvas.create_line(poster_center_x, self.canvas_offset_y,
                                   poster_center_x, self.canvas_offset_y + display_h,
                                   fill="#ff00ff", width=1, dash=(5, 5), tags="guide")
            
            # 水平中线
            self.canvas.create_line(self.canvas_offset_x, poster_center_y,
                                   self.canvas_offset_x + display_w, poster_center_y,
                                   fill="#ff00ff", width=1, dash=(5, 5), tags="guide")
            
            self.guide_lines = [
                ('v', self.poster_img.width / 2),  # 垂直中线
                ('h', self.poster_img.height / 2)  # 水平中线
            ]
        
        # 绘制二维码
        if self.qr_img:
            qr_display_x = self.canvas_offset_x + self.qr_x * self.canvas_scale
            qr_display_y = self.canvas_offset_y + self.qr_y * self.canvas_scale
            qr_display_w = int(self.qr_w * self.canvas_scale)
            qr_display_h = int(self.qr_h * self.canvas_scale)
            
            if qr_display_w > 0 and qr_display_h > 0:
                qr_resized = self.qr_img.resize((qr_display_w, qr_display_h), Image.Resampling.LANCZOS)
                self.qr_photo = ImageTk.PhotoImage(qr_resized)
                self.canvas.create_image(qr_display_x, qr_display_y, 
                                        image=self.qr_photo, anchor=tk.NW, tags="qr")
                
                # 绘制边框
                self.canvas.create_rectangle(qr_display_x, qr_display_y,
                                            qr_display_x + qr_display_w,
                                            qr_display_y + qr_display_h,
                                            outline="#00ff00", width=2, tags="qr_border")
                
                # 绘制四个角的缩放手柄
                handle_size = 10
                handles = [
                    (qr_display_x, qr_display_y, "tl"),  # 左上
                    (qr_display_x + qr_display_w, qr_display_y, "tr"),  # 右上
                    (qr_display_x, qr_display_y + qr_display_h, "bl"),  # 左下
                    (qr_display_x + qr_display_w, qr_display_y + qr_display_h, "br")  # 右下
                ]
                
                for hx, hy, tag in handles:
                    self.canvas.create_rectangle(hx - handle_size/2, hy - handle_size/2,
                                                hx + handle_size/2, hy + handle_size/2,
                                                fill="#00ff00", outline="white", width=1,
                                                tags=f"handle_{tag}")
        
        self.update_info_display()
    
    def update_info_display(self):
        """更新信息显示"""
        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        info = f"位置: ({int(self.qr_x)}, {int(self.qr_y)})\n"
        info += f"尺寸: {int(self.qr_w)} × {int(self.qr_h)}\n"
        if self.poster_img:
            info += f"海报: {self.poster_img.width} × {self.poster_img.height}\n"
        info += f"缩放: {self.canvas_scale:.2f}x"
        self.info_text.insert(1.0, info)
        self.info_text.configure(state=tk.DISABLED)
    
    def on_canvas_mousewheel(self, event):
        """Ctrl+滚轮缩放视图"""
        # 用户手动缩放后，禁用自动适配
        self.auto_fit_enabled = False
        
        if event.num == 4 or event.delta > 0:
            scale_factor = 1.1
        else:
            scale_factor = 0.9
        
        old_scale = self.canvas_scale
        self.canvas_scale *= scale_factor
        self.canvas_scale = max(0.1, min(self.canvas_scale, 5.0))
        
        # 以鼠标位置为中心缩放
        mouse_x = event.x
        mouse_y = event.y
        
        self.canvas_offset_x = mouse_x - (mouse_x - self.canvas_offset_x) * (self.canvas_scale / old_scale)
        self.canvas_offset_y = mouse_y - (mouse_y - self.canvas_offset_y) * (self.canvas_scale / old_scale)
        
        self.redraw_canvas()

    
    def on_pan_mousewheel(self, event):
        """Shift+滚轮平移视图"""
        if event.num == 4 or event.delta > 0:
            self.canvas_offset_y += 20
        else:
            self.canvas_offset_y -= 20
        self.redraw_canvas()
    
    def on_pan_press(self, event):
        """中键按下开始拖拽背景"""
        self.drag_mode = "pan"
        self.dragging = True
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.canvas.configure(cursor="fleur")
    
    def on_pan_drag(self, event):
        """中键拖拽背景"""
        if self.drag_mode == "pan":
            dx = event.x - self.drag_start_x
            dy = event.y - self.drag_start_y
            
            self.canvas_offset_x += dx
            self.canvas_offset_y += dy
            
            self.drag_start_x = event.x
            self.drag_start_y = event.y
            
            self.redraw_canvas()
    
    def on_pan_release(self, event):
        """中键释放结束拖拽"""
        if self.drag_mode == "pan":
            self.drag_mode = None
            self.dragging = False
            self.canvas.configure(cursor="arrow")
    
    def on_canvas_press(self, event):
        if not self.qr_img:
            return
        
        # 检查是否点击了手柄
        for tag in ["tl", "tr", "bl", "br"]:
            items = self.canvas.find_withtag(f"handle_{tag}")
            if items:
                coords = self.canvas.coords(items[0])
                cx = (coords[0] + coords[2]) / 2
                cy = (coords[1] + coords[3]) / 2
                if abs(event.x - cx) < 15 and abs(event.y - cy) < 15:
                    self.drag_mode = f"resize_{tag}"
                    self.dragging = True
                    self.drag_start_x = event.x
                    self.drag_start_y = event.y
                    return
        
        # 检查是否点击了二维码区域
        qr_display_x = self.canvas_offset_x + self.qr_x * self.canvas_scale
        qr_display_y = self.canvas_offset_y + self.qr_y * self.canvas_scale
        qr_display_w = self.qr_w * self.canvas_scale
        qr_display_h = self.qr_h * self.canvas_scale
        
        if (qr_display_x <= event.x <= qr_display_x + qr_display_w and
            qr_display_y <= event.y <= qr_display_y + qr_display_h):
            self.drag_mode = "move"
            self.dragging = True
            self.drag_start_x = event.x
            self.drag_start_y = event.y
    
    def on_canvas_drag(self, event):
        if not self.dragging or self.drag_mode == "pan":
            return
        
        dx = (event.x - self.drag_start_x) / self.canvas_scale
        dy = (event.y - self.drag_start_y) / self.canvas_scale
        
        if self.drag_mode == "move":
            self.qr_x += dx
            self.qr_y += dy
            
            # 智能对齐
            if self.snap_enabled.get() and self.poster_img:
                threshold = self.snap_threshold / self.canvas_scale
                
                # 检查二维码中心是否接近海报中心
                qr_center_x = self.qr_x + self.qr_w / 2
                qr_center_y = self.qr_y + self.qr_h / 2
                poster_center_x = self.poster_img.width / 2
                poster_center_y = self.poster_img.height / 2
                
                # 吸附到垂直中线
                new_center_x, snapped_x = self.calculate_snap_position(qr_center_x, poster_center_x, threshold)
                if snapped_x:
                    self.qr_x = new_center_x - self.qr_w / 2
                
                # 吸附到水平中线
                new_center_y, snapped_y = self.calculate_snap_position(qr_center_y, poster_center_y, threshold)
                if snapped_y:
                    self.qr_y = new_center_y - self.qr_h / 2
            
            # 限制在海报范围内
            if self.poster_img:
                self.qr_x = max(0, min(self.qr_x, self.poster_img.width - self.qr_w))
                self.qr_y = max(0, min(self.qr_y, self.poster_img.height - self.qr_h))
        
        elif self.drag_mode == "resize_br":  # 右下角
            if self.aspect_ratio_locked.get():
                # 等比例缩放，以对角线距离为基准
                avg_delta = (dx + dy) / 2
                self.qr_w = max(20, self.qr_w + avg_delta)
                self.qr_h = self.qr_w / self.original_aspect_ratio
            else:
                self.qr_w = max(20, self.qr_w + dx)
                self.qr_h = max(20, self.qr_h + dy)
        
        elif self.drag_mode == "resize_tl":  # 左上角
            if self.aspect_ratio_locked.get():
                avg_delta = (dx + dy) / 2
                new_w = max(20, self.qr_w - avg_delta)
                new_h = new_w / self.original_aspect_ratio
                self.qr_x += self.qr_w - new_w
                self.qr_y += self.qr_h - new_h
                self.qr_w = new_w
                self.qr_h = new_h
            else:
                new_w = max(20, self.qr_w - dx)
                new_h = max(20, self.qr_h - dy)
                self.qr_x += self.qr_w - new_w
                self.qr_y += self.qr_h - new_h
                self.qr_w = new_w
                self.qr_h = new_h
        
        elif self.drag_mode == "resize_tr":  # 右上角
            if self.aspect_ratio_locked.get():
                avg_delta = (dx - dy) / 2
                new_w = max(20, self.qr_w + avg_delta)
                new_h = new_w / self.original_aspect_ratio
                self.qr_y += self.qr_h - new_h
                self.qr_w = new_w
                self.qr_h = new_h
            else:
                new_h = max(20, self.qr_h - dy)
                self.qr_y += self.qr_h - new_h
                self.qr_w = max(20, self.qr_w + dx)
                self.qr_h = new_h
        
        elif self.drag_mode == "resize_bl":  # 左下角
            if self.aspect_ratio_locked.get():
                avg_delta = (-dx + dy) / 2
                new_w = max(20, self.qr_w + avg_delta)
                new_h = new_w / self.original_aspect_ratio
                self.qr_x += self.qr_w - new_w
                self.qr_w = new_w
                self.qr_h = new_h
            else:
                new_w = max(20, self.qr_w - dx)
                self.qr_x += self.qr_w - new_w
                self.qr_w = new_w
                self.qr_h = max(20, self.qr_h + dy)
        
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        
        # 实时更新输入框
        self.update_input_fields()
        self.redraw_canvas()
    
    def on_canvas_release(self, event):
        if self.dragging and self.drag_mode != "pan":
            # 操作完成后保存状态
            self.save_state()
            self.status_label.configure(text="就绪", foreground="blue")
        
        self.dragging = False
        self.drag_mode = None
    
    def on_canvas_motion(self, event):
        # 改变鼠标光标
        if not self.qr_img:
            return
        
        cursor = "arrow"
        
        # 检查手柄
        for tag in ["tl", "tr", "bl", "br"]:
            items = self.canvas.find_withtag(f"handle_{tag}")
            if items:
                coords = self.canvas.coords(items[0])
                cx = (coords[0] + coords[2]) / 2
                cy = (coords[1] + coords[3]) / 2
                if abs(event.x - cx) < 15 and abs(event.y - cy) < 15:
                    if tag in ["tl", "br"]:
                        cursor = "size_nw_se"
                    else:
                        cursor = "size_ne_sw"
                    break
        
        # 检查二维码区域
        if cursor == "arrow":
            qr_display_x = self.canvas_offset_x + self.qr_x * self.canvas_scale
            qr_display_y = self.canvas_offset_y + self.qr_y * self.canvas_scale
            qr_display_w = self.qr_w * self.canvas_scale
            qr_display_h = self.qr_h * self.canvas_scale
            
            if (qr_display_x <= event.x <= qr_display_x + qr_display_w and
                qr_display_y <= event.y <= qr_display_y + qr_display_h):
                cursor = "fleur"
        
        self.canvas.configure(cursor=cursor)
    
    def start_processing(self):
        self.progress['value'] = 0
        self.status_label.configure(text="正在处理...", foreground="orange")
        self.process_btn.configure(state=tk.DISABLED)
        
        thread = threading.Thread(target=self.process_images)
        thread.daemon = True
        thread.start()

    def process_images(self):
        error_msg = None
        success_count = 0
        
        try:
            qr_files = [f for f in os.listdir(self.qr_folder_str) 
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            
            total = len(qr_files)
            os.makedirs(self.output_folder_str, exist_ok=True)
            
            output_format = self.output_format.get()
            jpeg_quality = 95
            
            if output_format == "jpeg":
                try:
                    jpeg_quality = int(self.quality_var.get())
                    jpeg_quality = max(1, min(100, jpeg_quality))
                except ValueError:
                    jpeg_quality = 95
            
            # 预处理
            target_size = (int(self.qr_w), int(self.qr_h))
            target_pos = (int(self.qr_x), int(self.qr_y))
            
            if output_format == "jpeg":
                poster_base = self.poster_img.convert("RGB")
            else:
                poster_base = self.poster_img
            
            # 选择缩放算法
            if output_format == "jpeg" and jpeg_quality < 85:
                resample_method = Image.Resampling.BILINEAR
            else:
                resample_method = Image.Resampling.LANCZOS
            
            # ========== 多线程处理函数 ==========
            def process_single_image(args):
                i, qr_filename = args
                try:
                    qr_path = os.path.join(self.qr_folder_str, qr_filename)
                    
                    # base_name = os.path.splitext(qr_filename)[0]
                    # if output_format == "png":
                    #     output_filename = base_name + ".png"
                    # else:
                    #     output_filename = base_name + ".jpg"
                    # 使用自定义命名规则生成文件名
                    output_filename = self.generate_filename(qr_filename, i)

                    output_path = os.path.join(self.output_folder_str, output_filename)
                    
                    # 加载和缩放
                    qr = Image.open(qr_path)
                    
                    if qr.mode != "RGBA" and output_format == "png":
                        qr = qr.convert("RGBA")
                    elif qr.mode == "RGBA" and output_format == "jpeg":
                        qr = qr.convert("RGB")
                    
                    qr_resized = qr.resize(target_size, resample_method)
                    
                    # 合成
                    result = poster_base.copy()
                    
                    if output_format == "png" and qr_resized.mode == "RGBA":
                        result.paste(qr_resized, target_pos, qr_resized)
                    else:
                        result.paste(qr_resized, target_pos)
                    
                    # 保存
                    if output_format == "png":
                        result.save(output_path, format='PNG', optimize=True)
                    else:
                        result.save(output_path, format='JPEG', quality=jpeg_quality, optimize=True)
                    
                    return True
                    
                except Exception as file_error:
                    print(f"处理文件 {qr_filename} 时出错: {file_error}")
                    return False
            
            # ========== 使用线程池并行处理 ==========
            max_workers = min(multiprocessing.cpu_count(), 4)  # 最多4个线程
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                futures = [executor.submit(process_single_image, (i, qr_filename)) 
                        for i, qr_filename in enumerate(qr_files)]
                
                # 收集结果并更新进度
                for i, future in enumerate(futures):
                    if future.result():
                        success_count += 1
                    
                    # 更新进度（每10个更新一次）
                    if i % 10 == 0 or i == total - 1:
                        progress = int((i + 1) / total * 100)
                        self.root.after(0, self.update_progress, progress, i + 1, total)
            
            # 显示完成信息
            format_text = "PNG" if output_format == "png" else f"JPEG (质量{jpeg_quality})"
            self.root.after(0, lambda: self.status_label.configure(text="✅ 处理完成!", foreground="green"))
            self.root.after(0, lambda: messagebox.showinfo("完成", 
                f"已成功合成 {success_count}/{total} 张图片！\n输出格式: {format_text}"))
            
        except Exception as ex:
            error_msg = str(ex)
            self.root.after(0, lambda msg=error_msg: self.status_label.configure(text=f"❌ 失败", foreground="red"))
            self.root.after(0, lambda msg=error_msg: messagebox.showerror("错误", f"处理失败：{msg}"))
        
        finally:
            self.root.after(0, lambda: self.process_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.progress.configure(value=0))

    
    def update_progress(self, progress_value, current, total):
        """更新进度条和状态"""
        self.progress.configure(value=progress_value)
        self.status_label.configure(text=f"处理中 {current}/{total}")
    
    def compress_output(self):
        """压缩输出文件夹"""
        try:
            compress_type = self.compress_option.get()
            output_folder = self.output_folder_str
            
            if compress_type == "zip":
                zip_path = output_folder + ".zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(output_folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, os.path.dirname(output_folder))
                            zipf.write(file_path, arcname)
                print(f"已创建ZIP压缩包: {zip_path}")
            
            elif compress_type == "gzip":
                tar_path = output_folder + ".tar.gz"
                with tarfile.open(tar_path, "w:gz") as tar:
                    tar.add(output_folder, arcname=os.path.basename(output_folder))
                print(f"已创建TAR.GZ压缩包: {tar_path}")
                
        except Exception as e:
            print(f"压缩时出错: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = InteractiveQRPosterGenerator(root)
    root.mainloop()
